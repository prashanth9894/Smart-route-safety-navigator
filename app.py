from flask import Flask, render_template, request, jsonify
import pandas as pd
import requests
import math

app = Flask(__name__)

# Helper to convert Place Name to Coordinates using Nominatim
def get_coordinates(place_name):
    url = f"https://nominatim.openstreetmap.org/search?q={place_name}&format=json&limit=1"
    headers = {'User-Agent': 'SafeRouteNavigator/1.0'} # Required by Nominatim policy
    try:
        response = requests.get(url, headers=headers, timeout=5).json()
        if response:
            return response[0]['lon'], response[0]['lat']
        return None
    except Exception:
        return None

def haversine(lat1, lon1, lat2, lon2):
    """Calculate distance between two coordinates in km"""
    R = 6371
    dlat, dlon = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * \
        math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))

def calculate_safety(geometry, crime_df):
    """Calculate safety score for a route based on crime proximity"""
    total_risk = 0
    unsafe_pins = []
    
    # Sample every 5th point to optimize performance
    for i in range(0, len(geometry), 5):
        lon, lat = geometry[i]
        
        for _, crime in crime_df.iterrows():
            distance = haversine(lat, lon, float(crime['latitude']), float(crime['longitude']))
            
            # Within 300m radius
            if distance < 0.3:
                severity = crime['severity']
                
                # Negative severity = safety points (police, CCTV)
                if severity < 0:
                    total_risk += severity  # Reduces total risk
                else:
                    total_risk += severity
                    
                unsafe_pins.append({
                    "lat": float(crime['latitude']),
                    "lng": float(crime['longitude']),
                    "type": crime['type'],
                    "desc": crime['description'],
                    "severity": int(severity)
                })
    
    # Remove duplicates
    unique_pins = [dict(t) for t in {tuple(d.items()) for d in unsafe_pins}]
    
    # Safety score: 100 = perfect, 0 = very dangerous
    safety_score = max(0, min(100, 100 - (total_risk * 2)))
    
    return round(safety_score, 2), unique_pins


# ============================================================================
# FEATURE 1: TRIPLE-ROUTE NAVIGATION
# ============================================================================

def get_multiple_routes(origin_coords, dest_coords, num_alternatives=3):
    """Fetch multiple alternative routes from OSRM"""
    origin = f"{origin_coords[0]},{origin_coords[1]}"
    dest = f"{dest_coords[0]},{dest_coords[1]}"
    
    # OSRM supports alternatives parameter
    osrm_url = f"http://router.project-osrm.org/route/v1/walking/{origin};{dest}?overview=full&geometries=geojson&alternatives={num_alternatives}"
    
    try:
        r = requests.get(osrm_url, timeout=5).json()
        if r.get('code') != 'Ok':
            return None
        return r.get('routes', [])
    except Exception:
        return None

def classify_route_safety(score):
    """Classify route as Safe, Moderate, or Risky"""
    if score >= 70:
        return "safe", "#2e7d32", "Safest Route ✓"
    elif score >= 40:
        return "moderate", "#f57c00", "Moderate Route ⚠"
    else:
        return "risky", "#d32f2f", "Risky Route ✗"


# ============================================================================
# FEATURE 2: HEATMAP DATA
# ============================================================================

def get_heatmap_data(crime_df):
    """Generate heatmap data points with intensity based on severity"""
    heatmap_points = []
    
    for _, crime in crime_df.iterrows():
        severity = crime['severity']
        
        # Only include danger zones (positive severity)
        if severity > 0:
            # Normalize intensity: severity 10 = intensity 1.0
            intensity = min(severity / 10, 1.0)
            
            heatmap_points.append({
                "lat": float(crime['latitude']),
                "lng": float(crime['longitude']),
                "intensity": intensity
            })
    
    return heatmap_points


# ============================================================================
# FEATURE 3: SAFE-HAVEN LOCATOR
# ============================================================================

def find_nearest_safe_havens(lat, lng, crime_df, limit=3):
    """Find nearest police stations, CCTV zones, and hospitals"""
    safe_havens = []
    
    # Filter for safety points (negative severity)
    safety_df = crime_df[crime_df['severity'] < 0].copy()
    
    for _, point in safety_df.iterrows():
        distance = haversine(lat, lng, float(point['latitude']), float(point['longitude']))
        
        safe_havens.append({
            "type": point['type'],
            "lat": float(point['latitude']),
            "lng": float(point['longitude']),
            "distance": round(distance, 2),
            "description": point['description']
        })
    
    # Sort by distance and return top N
    safe_havens.sort(key=lambda x: x['distance'])
    return safe_havens[:limit]


# ============================================================================
# FEATURE 4: SOS CONTEXT DATA
# ============================================================================

def get_sos_context(lat, lng, crime_df, route_geometry=None):
    """Generate SOS alert context with nearby dangers and route info"""
    nearby_dangers = []
    
    # Find all high-severity incidents within 500m
    danger_df = crime_df[crime_df['severity'] >= 7].copy()
    
    for _, danger in danger_df.iterrows():
        distance = haversine(lat, lng, float(danger['latitude']), float(danger['longitude']))
        
        if distance < 0.5:  # Within 500m
            nearby_dangers.append({
                "type": danger['type'],
                "severity": int(danger['severity']),
                "distance": round(distance * 1000, 0),  # in meters
                "description": danger['description']
            })
    
    # Sort by severity and distance
    nearby_dangers.sort(key=lambda x: (-x['severity'], x['distance']))
    
    return {
        "user_location": {"lat": lat, "lng": lng},
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "nearby_dangers": nearby_dangers[:5],  # Top 5 closest high-risk areas
        "safe_havens": find_nearest_safe_havens(lat, lng, crime_df, limit=2)
    }


# ============================================================================
# FLASK ROUTES
# ============================================================================

@app.route("/")
def index():
    """Main page - load map with initial crime pins"""
    df = pd.read_csv('crime_history.csv')
    all_pins = df.to_dict(orient='records')
    return render_template("index.html", initial_pins=all_pins)


@app.route("/get_triple_routes", methods=["POST"])
def get_triple_routes():
    """
    FEATURE 1: Return 3 alternative routes ranked by safety
    Request: {origin: "place name", destination: "place name"}
    Response: {routes: [{geometry, safety_score, category, pins, distance}...]}
    """
    data = request.json
    origin_name = data.get('origin')
    dest_name = data.get('destination')
    
    # Convert names to coordinates
    origin_coords = get_coordinates(origin_name)
    dest_coords = get_coordinates(dest_name)
    
    if not origin_coords or not dest_coords:
        return jsonify({"error": "Could not find one or both locations. Try being more specific."}), 400
    
    # Get multiple routes from OSRM
    routes = get_multiple_routes(origin_coords, dest_coords, num_alternatives=3)
    
    if not routes:
        return jsonify({"error": "No routes found between these locations."}), 400
    
    df = pd.read_csv('crime_history.csv')
    
    # Score each route
    scored_routes = []
    for route in routes:
        score, pins = calculate_safety(route['geometry']['coordinates'], df)
        category, color, label = classify_route_safety(score)
        
        scored_routes.append({
            "geometry": route['geometry'],
            "safety_score": score,
            "category": category,
            "color": color,
            "label": label,
            "pins": pins,
            "distance": f"{round(route['distance']/1000, 2)} km",
            "duration": f"{round(route['duration']/60, 0)} min"
        })
    
    # Sort by safety score (descending)
    scored_routes.sort(key=lambda x: x['safety_score'], reverse=True)
    
    return jsonify({"routes": scored_routes})


@app.route("/get_heatmap", methods=["GET"])
def get_heatmap():
    """
    FEATURE 2: Return heatmap data for crime visualization
    Response: {heatmap_points: [{lat, lng, intensity}...]}
    """
    df = pd.read_csv('crime_history.csv')
    heatmap_points = get_heatmap_data(df)
    
    return jsonify({"heatmap_points": heatmap_points})


@app.route("/find_safe_havens", methods=["POST"])
def find_safe_havens():
    """
    FEATURE 3: Find nearest police stations and safe zones
    Request: {lat: float, lng: float}
    Response: {safe_havens: [{type, lat, lng, distance, description}...]}
    """
    data = request.json
    lat = data.get('lat')
    lng = data.get('lng')
    
    if not lat or not lng:
        return jsonify({"error": "Coordinates required"}), 400
    
    df = pd.read_csv('crime_history.csv')
    safe_havens = find_nearest_safe_havens(float(lat), float(lng), df, limit=5)
    
    return jsonify({"safe_havens": safe_havens})


@app.route("/send_sos", methods=["POST"])
def send_sos():
    """
    FEATURE 4: Generate SOS alert context
    Request: {lat: float, lng: float, route_geometry: optional}
    Response: {sos_context: {user_location, timestamp, nearby_dangers, safe_havens}}
    """
    data = request.json
    lat = data.get('lat')
    lng = data.get('lng')
    route_geometry = data.get('route_geometry')
    
    if not lat or not lng:
        return jsonify({"error": "Location required"}), 400
    
    df = pd.read_csv('crime_history.csv')
    sos_context = get_sos_context(float(lat), float(lng), df, route_geometry)
    
    return jsonify({"sos_context": sos_context})


if __name__ == "__main__":
    app.run(debug=True)