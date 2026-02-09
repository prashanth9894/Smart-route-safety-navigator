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
            # Nominatim returns lat/lon as strings
            return response[0]['lon'], response[0]['lat']
        return None
    except Exception:
        return None

def haversine(lat1, lon1, lat2, lon2):
    R = 6371 
    dlat, dlon = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * \
        math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))

def calculate_safety(geometry, crime_df):
    total_risk = 0
    unsafe_pins = []
    for i in range(0, len(geometry), 5):
        lon, lat = geometry[i]
        for _, crime in crime_df.iterrows():
            if haversine(lat, lon, float(crime['latitude']), float(crime['longitude'])) < 0.3:
                total_risk += crime['severity']
                unsafe_pins.append({
                    "lat": float(crime['latitude']), 
                    "lng": float(crime['longitude']),
                    "type": crime['type'], 
                    "desc": crime['description']
                })
    unique_pins = [dict(t) for t in {tuple(d.items()) for d in unsafe_pins}]
    safety_score = max(0, 100 - (total_risk * 2))
    return round(safety_score, 2), unique_pins

@app.route("/")
def index():
    df = pd.read_csv('crime_history.csv')
    all_pins = df.to_dict(orient='records')
    return render_template("index.html", initial_pins=all_pins)

@app.route("/get_safe_route", methods=["POST"])
def get_safe_route():
    data = request.json
    origin_name = data.get('origin')
    dest_name = data.get('destination')

    # Convert names to coordinates
    origin_coords = get_coordinates(origin_name)
    dest_coords = get_coordinates(dest_name)

    if not origin_coords or not dest_coords:
        return jsonify({"error": "Could not find one or both locations. Try being more specific."}), 400

    # Format for OSRM: lon,lat
    origin = f"{origin_coords[0]},{origin_coords[1]}"
    dest = f"{dest_coords[0]},{dest_coords[1]}"

    osrm_url = f"http://router.project-osrm.org/route/v1/walking/{origin};{dest}?overview=full&geometries=geojson"
    
    try:
        r = requests.get(osrm_url, timeout=5).json()
        if r.get('code') != 'Ok':
            return jsonify({"error": "No walking route found between these places."}), 400

        df = pd.read_csv('crime_history.csv')
        route = r['routes'][0]
        score, path_pins = calculate_safety(route['geometry']['coordinates'], df)

        return jsonify({
            "safety_score": score,
            "pins": path_pins,
            "geometry": route['geometry'],
            "distance": f"{round(route['distance']/1000, 2)} km"
        })
    except Exception as e:
        return jsonify({"error": "Connection to routing server failed."}), 500

if __name__ == "__main__":
    app.run(debug=True)