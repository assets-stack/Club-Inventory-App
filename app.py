import json
import os
import requests
import base64
from datetime import datetime
from flask import Flask, render_template, request, jsonify, redirect, url_for
from firebase_admin import credentials, initialize_app, firestore

# --- Global Configuration and Initialization ---

# MANDATORY variables provided by the environment
app_id = os.environ.get('__app_id', 'default-inventory-app')
# The primary expected variable, but we will now check for the file path as well.
firebase_config_json = os.environ.get('__firebase_config') 

github_token = os.environ.get('__github_token', 'YOUR_GITHUB_TOKEN')
github_username = os.environ.get('__github_username', 'YOUR_GITHUB_USERNAME')
github_repo = os.environ.get('__github_repo', 'YOUR_GITHUB_REPO')

# Firestore paths
FIRESTORE_COLLECTION = f"artifacts/{app_id}/public/data/inventory_items"
FIREBASE_CONFIG = {}
db = None # Initialize db as None

# Define the expected path for the secret file based on your environment's guidance
SECRET_FILE_PATH = "/etc/secrets/serviceAccountKey.json"

# --- Mock Data Storage (Used if Firebase connection fails) ---
MOCK_INVENTORY = [
    {
        'id': 'mock-1', 
        'name': 'Sony a7S III', 
        'category': 'Cameras', 
        'status': 'Available', 
        'photo_url': 'https://placehold.co/80x80/0d9488/ffffff?text=Camera'
    },
    {
        'id': 'mock-2', 
        'name': 'Zoom H6 Recorder', 
        'category': 'Audio', 
        'status': 'Checked Out', 
        'photo_url': 'https://placehold.co/80x80/7e22ce/ffffff?text=Audio'
    },
    {
        'id': 'mock-3', 
        'name': '50mm f/1.4 Lens', 
        'category': 'Lenses', 
        'status': 'Available', 
        'photo_url': 'https://placehold.co/80x80/059669/ffffff?text=Lens'
    },
]

# Initialize Firebase
try:
    # 1. Check for the Secret File first (based on your environment setup)
    if os.path.exists(SECRET_FILE_PATH):
        print(f"INFO: Found Firebase secret file at {SECRET_FILE_PATH}. Attempting to load credentials from file.")
        with open(SECRET_FILE_PATH, 'r') as f:
            FIREBASE_CONFIG = json.load(f)
            firebase_config_json = json.dumps(FIREBASE_CONFIG) # Set variable for consistency
    
    # 2. If the file was not found, check the environment variable (original method)
    elif firebase_config_json:
        print("INFO: Found Firebase credentials in '__firebase_config' environment variable.")
        FIREBASE_CONFIG = json.loads(firebase_config_json)

    # 3. If neither method provided config, log a critical warning and proceed in MOCK mode
    if not firebase_config_json:
        print("CRITICAL WARNING: No Firebase config found via environment variable OR secret file path. Running in MOCK DATA MODE.")
        # Proceeding without config
    else:
        # Now FIREBASE_CONFIG is populated (either from file or environment variable)
        if FIREBASE_CONFIG and 'private_key' in FIREBASE_CONFIG:
            cred = credentials.Certificate(FIREBASE_CONFIG)
            initialize_app(cred)
            db = firestore.client()
            print("Firebase successfully initialized with service account.")
            print("Firestore client successfully obtained.")
        else:
            print("WARNING: Firebase config is present but missing the 'private_key'. Running in MOCK DATA MODE.")
            initialize_app()


except json.JSONDecodeError as e:
    print(f"CRITICAL ERROR: Failed to decode Firebase credentials JSON: {e}. Running in MOCK DATA MODE.")
except Exception as e:
    print(f"CRITICAL ERROR: Failed to initialize Firebase or get client: {e}. Running in MOCK DATA MODE.")


# --- Flask App Setup ---
app = Flask(__name__)

# --- GitHub Utility Function (Unchanged, but will only run if credentials are set) ---

def upload_to_github(file_data, filename):
    """
    Uploads a file to a specific path in the configured GitHub repository.
    Returns the raw public URL to the file, suitable for the web application.
    """
    # Check for placeholder values
    if github_token == 'YOUR_GITHUB_TOKEN' or github_username == 'YOUR_GITHUB_USERNAME' or github_repo == 'YOUR_GITHUB_REPO':
        print("GitHub credentials missing or using placeholders. Cannot upload photo.")
        return None
        
    if not github_token or not github_username or not github_repo:
        print("GitHub credentials missing. Cannot upload photo.")
        return None

    # Encode file data to Base64
    encoded_content = base64.b64encode(file_data.read()).decode('utf-8')
    
    # Use a unique, timestamped path within the repo
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    path = f"inventory_photos/{timestamp}_{filename}"

    url = f"https://api.github.com/repos/{github_username}/{github_repo}/contents/{path}"
    
    headers = {
        "Authorization": f"token {github_token}",
        "Accept": "application/vnd.github.v3.raw",
    }
    
    payload = {
        "message": f"Add {filename} for inventory item",
        "content": encoded_content
    }

    try:
        response = requests.put(url, headers=headers, json=payload)
        response.raise_for_status() # Raises HTTPError for bad responses (4xx or 5xx)
        
        # Construct the raw GitHub Pages URL for public access
        raw_url = f"https://{github_username}.github.io/{github_repo}/{path}"
        
        print(f"Successfully uploaded to GitHub. Raw URL: {raw_url}")
        return raw_url

    except requests.exceptions.RequestException as e:
        print(f"GitHub API Error: {e}")
        try:
            # Print response content if available for better debugging
            print("GitHub API Response Content:", response.json())
        except:
            pass
        return None

# --- Flask Routes ---

@app.route('/')
def inventory_homepage():
    """Renders the main application page."""
    return render_template('index.html')

# --- API Endpoints ---

@app.route('/api/items', methods=['GET'])
def get_inventory():
    """Fetches all items from Firestore or returns mock data if connection fails."""
    if not db:
        print("Returning MOCK data.")
        # Ensure mock data is returned sorted by name
        return jsonify(sorted(MOCK_INVENTORY, key=lambda x: x.get('name', ''))), 200

    try:
        items_ref = db.collection(FIRESTORE_COLLECTION).stream()
        inventory = []
        for doc in items_ref:
            item = doc.to_dict()
            item['id'] = doc.id
            inventory.append(item)
        
        inventory.sort(key=lambda x: x.get('name', ''))
            
        return jsonify(inventory), 200

    except Exception as e:
        print(f"Error fetching inventory from Firestore: {e}")
        # Even if DB is initialized, errors like permission denied can occur, so fall back to mock
        print("Falling back to MOCK DATA due to runtime Firestore error.")
        return jsonify(sorted(MOCK_INVENTORY, key=lambda x: x.get('name', ''))), 200


@app.route('/api/items', methods=['POST'])
def add_item():
    """Handles adding a new item, using mock logic if no DB connection."""
    name = request.form.get('name')
    category = request.form.get('category')
    photo_file = request.files.get('photo')

    if not name or not category:
        return jsonify({"error": "Name and Category are required."}), 400

    photo_url = None
    if photo_file and photo_file.filename:
        # Attempt to upload to GitHub only if file is present
        photo_url = upload_to_github(photo_file, photo_file.filename)
        # If photo upload fails, we still allow the item to be created (with a placeholder)
        if not photo_url:
            photo_url = 'https://placehold.co/80x80/e0e7ff/4338ca?text=Upload+Fail'


    new_item_data = {
        'name': name,
        'category': category,
        'status': 'Available',
        'photo_url': photo_url,
    }

    if not db:
        # MOCK MODE: Add to mock list and simulate a successful response
        new_item_data['id'] = f"mock-{len(MOCK_INVENTORY) + 1}"
        MOCK_INVENTORY.append(new_item_data)
        print(f"MOCK MODE: Added item {name}")
        return jsonify(new_item_data), 201
        
    try:
        # FIREBASE MODE
        doc_ref = db.collection(FIRESTORE_COLLECTION).add({
            **new_item_data, 
            'created_at': firestore.SERVER_TIMESTAMP
        })[1]
        new_item_data['id'] = doc_ref.id
        return jsonify(new_item_data), 201

    except Exception as e:
        print(f"Error adding item to Firestore: {e}")
        return jsonify({"error": "Failed to add item to inventory (Firestore error)."}), 500


@app.route('/api/items/<item_id>', methods=['DELETE'])
def delete_item(item_id):
    """Handles deleting an item by its ID, using mock logic if no DB connection."""
    if not db:
        # MOCK MODE: Find and remove from mock list
        global MOCK_INVENTORY
        initial_len = len(MOCK_INVENTORY)
        MOCK_INVENTORY = [item for item in MOCK_INVENTORY if item['id'] != item_id]
        if len(MOCK_INVENTORY) < initial_len:
            print(f"MOCK MODE: Deleted item {item_id}")
            return jsonify({"message": f"MOCK DELETED: Item {item_id} deleted successfully."}), 200
        return jsonify({"error": "MOCK ERROR: Item not found."}), 404

    try:
        doc_ref = db.collection(FIRESTORE_COLLECTION).document(item_id)
        if not doc_ref.get().exists:
            return jsonify({"error": "Item not found."}), 404
            
        doc_ref.delete()
        
        return jsonify({"message": f"Item {item_id} deleted successfully."}), 200

    except Exception as e:
        print(f"Error deleting item {item_id} from Firestore: {e}")
        return jsonify({"error": "Failed to delete item (Firestore error)."}), 500


@app.route('/api/items/<item_id>/status', methods=['PUT'])
def update_item_status(item_id):
    """Handles updating an item's status, using mock logic if no DB connection."""
    data = request.get_json()
    new_status = data.get('status')

    if new_status not in ['Available', 'Checked Out']:
        return jsonify({"error": "Invalid status value."}), 400

    if not db:
        # MOCK MODE: Update status in mock list
        for item in MOCK_INVENTORY:
            if item['id'] == item_id:
                item['status'] = new_status
                print(f"MOCK MODE: Updated status for {item_id} to {new_status}")
                return jsonify({"message": f"MOCK UPDATED: Status for item {item_id} updated to {new_status}."}), 200
        return jsonify({"error": "MOCK ERROR: Item not found."}), 404


    try:
        doc_ref = db.collection(FIRESTORE_COLLECTION).document(item_id)
        if not doc_ref.get().exists:
            return jsonify({"error": "Item not found."}), 404

        doc_ref.update({'status': new_status})
        
        return jsonify({"message": f"Status for item {item_id} updated to {new_status}."}), 200

    except Exception as e:
        print(f"Error updating item status in Firestore: {e}")
        return jsonify({"error": "Failed to update item status (Firestore error)."}), 500


if __name__ == '__main__':
    app.run(debug=True, port=8080)