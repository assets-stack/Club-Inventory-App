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
firebase_config_json = os.environ.get('__firebase_config')
github_token = os.environ.get('__github_token', 'YOUR_GITHUB_TOKEN')
github_username = os.environ.get('__github_username', 'YOUR_GITHUB_USERNAME')
github_repo = os.environ.get('__github_repo', 'YOUR_GITHUB_REPO')

# Firestore paths
FIRESTORE_COLLECTION = f"artifacts/{app_id}/public/data/inventory_items"
FIREBASE_CONFIG = {}

# Initialize Firebase
try:
    if firebase_config_json:
        FIREBASE_CONFIG = json.loads(firebase_config_json)

    # Check if a Firestore service account is available
    if FIREBASE_CONFIG and 'private_key' in FIREBASE_CONFIG:
        cred = credentials.Certificate(FIREBASE_CONFIG)
        initialize_app(cred)
        db = firestore.client()
        print("Firebase successfully initialized with service account.")
    else:
        # Fallback for environments without a service account (e.g., local testing)
        print("Firebase configuration missing private key. Using generic initialization (may fail in production).")
        initialize_app()
        db = firestore.client()

except Exception as e:
    print(f"Error initializing Firebase: {e}")
    db = None # Ensures no Firestore operations are attempted if initialization fails

# --- Flask App Setup ---
app = Flask(__name__)

# --- GitHub Utility Function ---

def upload_to_github(file_data, filename):
    """
    Uploads a file to a specific path in the configured GitHub repository.
    Returns the raw public URL to the file, suitable for the web application.
    """
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

        # The API response contains the content URL
        api_response_data = response.json()

        # Construct the raw GitHub Pages URL for public access
        # Format: https://<username>.github.io/<repo>/<path>
        raw_url = f"https://{github_username}.github.io/{github_repo}/{path}"

        print(f"Successfully uploaded to GitHub. Raw URL: {raw_url}")
        return raw_url

    except requests.exceptions.RequestException as e:
        print(f"GitHub API Error: {e}")
        # Print response content if available for better debugging
        try:
            print("GitHub API Response Content:", response.json())
        except:
            print("No JSON response from GitHub API.")
        return None

# --- Flask Routes ---

@app.route('/')
def inventory_homepage():
    """Renders the main application page."""
    return render_template('index.html')

# --- API Endpoints ---

@app.route('/api/items', methods=['GET'])
def get_inventory():
    """Fetches all items from Firestore and returns them as a JSON array."""
    if not db:
        return jsonify({"error": "Database not initialized."}), 500

    try:
        items_ref = db.collection(FIRESTORE_COLLECTION).stream()
        inventory = []
        for doc in items_ref:
            item = doc.to_dict()
            item['id'] = doc.id  # Use the Firestore document ID as the item ID
            inventory.append(item)

        # Sort items by name before sending to client for consistent display
        inventory.sort(key=lambda x: x.get('name', ''))

        return jsonify(inventory), 200

    except Exception as e:
        print(f"Error fetching inventory: {e}")
        return jsonify({"error": f"Failed to retrieve inventory: {e}"}), 500


@app.route('/api/items', methods=['POST'])
def add_item():
    """Handles adding a new item, including file upload to GitHub."""
    if not db:
        return jsonify({"error": "Database not initialized."}), 500

    name = request.form.get('name')
    category = request.form.get('category')
    photo_file = request.files.get('photo')

    if not name or not category:
        return jsonify({"error": "Name and Category are required."}), 400

    photo_url = None
    if photo_file and photo_file.filename:
        # Check file extension and content type for security
        filename = photo_file.filename

        # Upload file and get the public URL
        photo_url = upload_to_github(photo_file, filename)
        if not photo_url:
            return jsonify({"error": "Failed to upload photo to GitHub."}), 500

    try:
        # Initial status is 'Available'
        new_item = {
            'name': name,
            'category': category,
            'status': 'Available',
            'photo_url': photo_url,
            'created_at': firestore.SERVER_TIMESTAMP
        }

        # Add document to Firestore, letting Firestore generate the ID
        doc_ref = db.collection(FIRESTORE_COLLECTION).add(new_item)[1]

        new_item['id'] = doc_ref.id # Add ID to the response

        # Convert timestamp object to string for JSON response
        new_item.pop('created_at', None)

        return jsonify(new_item), 201

    except Exception as e:
        print(f"Error adding item: {e}")
        return jsonify({"error": "Failed to add item to inventory."}), 500


@app.route('/api/items/<item_id>', methods=['DELETE'])
def delete_item(item_id):
    """Handles deleting an item by its ID."""
    if not db:
        return jsonify({"error": "Database not initialized."}), 500

    try:
        doc_ref = db.collection(FIRESTORE_COLLECTION).document(item_id)
        # Check if item exists before attempting delete (optional, but good practice)
        if not doc_ref.get().exists:
            return jsonify({"error": "Item not found."}), 404

        doc_ref.delete()

        return jsonify({"message": f"Item {item_id} deleted successfully."}), 200

    except Exception as e:
        print(f"Error deleting item {item_id}: {e}")
        return jsonify({"error": "Failed to delete item."}), 500


@app.route('/api/items/<item_id>/status', methods=['PUT'])
def update_item_status(item_id):
    """Handles updating an item's status by its ID."""
    if not db:
        return jsonify({"error": "Database not initialized."}), 500

    try:
        data = request.get_json()
        new_status = data.get('status')

        if new_status not in ['Available', 'Checked Out']:
            return jsonify({"error": "Invalid status value."}), 400

        doc_ref = db.collection(FIRESTORE_COLLECTION).document(item_id)

        # Ensure the item exists
        if not doc_ref.get().exists:
            return jsonify({"error": "Item not found."}), 404

        doc_ref.update({'status': new_status})

        return jsonify({"message": f"Status for item {item_id} updated to {new_status}."}), 200

    except Exception as e:
        print(f"Error updating item status: {e}")
        return jsonify({"error": "Failed to update item status."}), 500


if __name__ == '__main__':
    # Flask runs directly on port 8080 in this environment
    app.run(debug=True, port=8080)