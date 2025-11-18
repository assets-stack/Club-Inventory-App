import json
import os
import requests
import base64
from datetime import datetime
from flask import Flask, render_template, request, jsonify, redirect, url_for
from firebase_admin import credentials, initialize_app, firestore

# --- Global Configuration and Initialization ---

# MANDATORY variables provided by the environment
app_id = os.environ.get('FLASK_APP', 'default-inventory-app')
firebase_config_json = os.environ.get('serviceAccountKey.json')

github_token = os.environ.get('GITHUB_TOKEN', 'YOUR_GITHUB_TOKEN')
github_username = os.environ.get('PHOTO_REPO_OWNER', 'YOUR_GITHUB_USERNAME')
github_repo = os.environ.get('PHOTO_REPO_NAME', 'YOUR_GITHUB_REPO')

# Firestore paths
FIRESTORE_COLLECTION = f"artifacts/{app_id}/public/data/inventory_items"
FIREBASE_CONFIG = {}
db = None

SECRET_FILE_PATH = "/etc/secrets/serviceAccountKey.json"

# Initialize Firebase
try:
    if os.path.exists(SECRET_FILE_PATH):
        print(f"INFO: Found Firebase secret file at {SECRET_FILE_PATH}. Attempting to load credentials from file.")
        with open(SECRET_FILE_PATH, 'r') as f:
            FIREBASE_CONFIG = json.load(f)
            firebase_config_json = json.dumps(FIREBASE_CONFIG)

    elif firebase_config_json:
        print("INFO: Found Firebase credentials in '__firebase_config' environment variable.")
        FIREBASE_CONFIG = json.loads(firebase_config_json)

    if not firebase_config_json:
        print("CRITICAL WARNING: No Firebase config found via environment variable OR secret file path.")
    else:
        if FIREBASE_CONFIG and 'private_key' in FIREBASE_CONFIG:
            cred = credentials.Certificate(FIREBASE_CONFIG)
            initialize_app(cred)
            db = firestore.client()
            print("Firebase successfully initialized with service account.")
            print("Firestore client successfully obtained.")
        else:
            print("WARNING: Firebase config is present but missing the 'private_key'.")
            initialize_app()


except json.JSONDecodeError as e:
    print(f"CRITICAL ERROR: Failed to decode Firebase credentials JSON: {e}.")
except Exception as e:
    print(f"CRITICAL ERROR: Failed to initialize Firebase or get client: {e}.")


# --- Flask App Setup ---
app = Flask(__name__)

# --- GitHub Utility Functions ---

def check_github_creds():
    """Checks if GitHub credentials are valid (not placeholders)."""
    return not (github_token == 'YOUR_GITHUB_TOKEN' or
                github_username == 'YOUR_GITHUB_USERNAME' or
                github_repo == 'YOUR_GITHUB_REPO')

def upload_to_github(file_data, filename):
    """Uploads a file and returns its raw public URL."""
    if not check_github_creds():
        print("GitHub credentials missing or using placeholders. Cannot upload photo.")
        return None

    encoded_content = base64.b64encode(file_data.read()).decode('utf-8')
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
        response.raise_for_status()

        # Construct the raw GitHub Pages URL for public access
        raw_url = f"https://{github_username}.github.io/{github_repo}/{path}"

        print(f"Successfully uploaded to GitHub. Raw URL: {raw_url}")
        return raw_url

    except requests.exceptions.RequestException as e:
        print(f"GitHub API Error during upload: {e}")
        return None


def get_github_file_path(photo_url):
    """Extracts the file path from the public GitHub Pages URL."""
    # Example URL: https://USERNAME.github.io/REPO/inventory_photos/20251116110000_image.jpg
    # We need: inventory_photos/20251116110000_image.jpg
    prefix = f"https://{github_username}.github.io/{github_repo}/"
    if photo_url and photo_url.startswith(prefix):
        return photo_url[len(prefix):]
    return None

def delete_from_github(photo_url):
    """Deletes a file from the GitHub repository using its public URL."""
    if not check_github_creds():
        print("GitHub credentials missing. Cannot delete photo.")
        return False

    file_path = get_github_file_path(photo_url)
    if not file_path:
        print(f"Warning: Could not parse file path from URL: {photo_url}")
        # Return True if it was a placeholder URL, as no cleanup is needed
        if photo_url.startswith('https://placehold.co/'):
            return True
        return False

    # 1. GET request to find the file's SHA hash (required for deletion)
    get_url = f"https://api.github.com/repos/{github_username}/{github_repo}/contents/{file_path}"
    headers = {
        "Authorization": f"token {github_token}",
        "Accept": "application/vnd.github.v3+json",
    }

    try:
        get_response = requests.get(get_url, headers=headers)
        # If the file is already gone (404), we treat it as successfully "deleted" for cleanup purposes
        if get_response.status_code == 404:
            print(f"Warning: File already missing from GitHub: {file_path}. No action needed.")
            return True

        get_response.raise_for_status()
        file_data = get_response.json()
        file_sha = file_data.get('sha')

        if not file_sha:
            print(f"Warning: SHA not found for file at path: {file_path}")
            return False

    except requests.exceptions.RequestException as e:
        print(f"Error fetching SHA for deletion of {file_path}: {e}")
        return False

    # 2. DELETE request using the SHA hash
    delete_url = get_url # Same URL as GET
    delete_payload = {
        "message": f"Delete file {file_path} for inventory item cleanup",
        "sha": file_sha
    }

    try:
        delete_response = requests.delete(delete_url, headers=headers, json=delete_payload)
        delete_response.raise_for_status()
        print(f"Successfully deleted photo from GitHub: {file_path}")
        return True

    except requests.exceptions.RequestException as e:
        print(f"Error deleting file from GitHub: {e}")
        return False


# --- Flask Routes ---

@app.route('/')
def inventory_homepage():
    """Renders the main application page."""
    return render_template('index.html')

# --- API Endpoints ---

@app.route('/api/items', methods=['GET'])
def get_inventory():
    """Fetches all items from Firestore"""
    try:
        items_ref = db.collection(FIRESTORE_COLLECTION).stream()
        inventory = []
        for doc in items_ref:
            item = doc.to_dict()
            item['id'] = doc.id
            inventory.append(item)

        # Sort by Asset Type then Name/Type_of_clothing for consistent display
        inventory.sort(key=lambda x: x.get('asset_type', '') + x.get('name', '') + x.get('type_of_clothing', ''))

        return jsonify(inventory), 200

    except Exception as e:
        print(f"Error fetching inventory from Firestore: {e}")
        return jsonify({"error": "Failed to fetch inventory (Firestore error)."}), 500

@app.route('/api/items', methods=['POST'])
def add_item():
    """Handles adding a new item based on asset_type."""
    asset_type = request.form.get('asset_type')
    photo_file = request.files.get('photo')

    if asset_type not in ['Costume', 'Prop', 'Set', 'Tech']:
        return jsonify({"error": "Invalid or missing Asset Type."}), 400

    photo_url = None
    if photo_file and photo_file.filename:
        photo_url = upload_to_github(photo_file, photo_file.filename)
        if not photo_url:
            photo_url = 'https://placehold.co/80x80/e0e7ff/4338ca?text=Upload+Fail'

    new_item_data = {
        'asset_type': asset_type,
        'status': 'Available',
        'photo_url': photo_url,
    }

    # --- Parse specific fields based on asset_type ---
    if asset_type == 'Costume':
        type_of_clothing = request.form.get('type_of_clothing')
        gender = request.form.get('gender')
        storage_location = request.form.get('storage_location')

        # Parse dynamic size/count rows
        sizes = request.form.getlist('size')
        counts = request.form.getlist('count')

        notes = request.form.get('notes')

        if not type_of_clothing or not storage_location or not sizes:
            return jsonify({"error": "Missing required fields for Costume asset."}), 400

        size_inventory = []
        for size, count in zip(sizes, counts):
            # Only add if both size and count are present and count is valid
            if size and count and count.isdigit() and int(count) > 0:
                size_inventory.append({'size': size, 'count': int(count)})

        if not size_inventory:
            return jsonify({"error": "Costume requires at least one valid Size and Number entry."}), 400

        new_item_data.update({
            'type_of_clothing': type_of_clothing,
            'gender': gender,
            'storage_location': storage_location,
            'size_inventory': size_inventory,
            'notes': notes if notes else None,
        })

    elif asset_type == 'Prop' or asset_type == 'Set' or asset_type == 'Tech':
        name = request.form.get('name')
        number = request.form.get('number')
        storage_location = request.form.get('storage_location')
        notes = request.form.get('notes')

        if not name or not number or not storage_location or not number.isdigit() or int(number) < 1:
            return jsonify({"error": f"Missing required fields or invalid number for {asset_type} asset."}), 400

        new_item_data.update({
            'name': name,
            'number': int(number),
            'storage_location': storage_location,
            'notes': notes if notes else None,
        })

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
        return jsonify({"error": "Failed to add asset to inventory (Firestore error)."}), 500


@app.route('/api/items/<item_id>', methods=['DELETE'])
def delete_item(item_id):
    """Handles deleting an item by its ID, now including GitHub cleanup."""

    # FIREBASE MODE
    try:
        doc_ref = db.collection(FIRESTORE_COLLECTION).document(item_id)
        doc = doc_ref.get()

        if not doc.exists:
            return jsonify({"error": "Item not found."}), 404

        item_data = doc.to_dict()
        photo_url = item_data.get('photo_url')

        # 1. Delete the item from Firestore
        doc_ref.delete()

        # 2. Delete the photo from GitHub (Fire and Forget)
        if photo_url:
            # Run the deletion, but don't hold up the user experience if it fails.
            delete_from_github(photo_url)

        return jsonify({"message": f"Item {item_id} deleted successfully."}), 200

    except Exception as e:
        print(f"Error deleting item {item_id} from Firestore: {e}")
        return jsonify({"error": "Failed to delete item (Firestore error)."}), 500


@app.route('/api/items/<item_id>/status', methods=['PUT'])
def update_item_status(item_id):
    """Handles updating an item's status"""
    data = request.get_json()
    new_status = data.get('status')

    if new_status not in ['Available', 'Checked Out']:
        return jsonify({"error": "Invalid status value."}), 400

    try:
        doc_ref = db.collection(FIRESTORE_COLLECTION).document(item_id)
        if not doc_ref.get().exists:
            return jsonify({"error": "Item not found."}), 404

        doc_ref.update({'status': new_status})

        return jsonify({"message": f"Status for item {item_id} updated to {new_status}."}), 200

    except Exception as e:
        print(f"Error updating item status in Firestore: {e}")
        return jsonify({"error": "Failed to update item status (Firestore error)."}), 500


@app.route('/api/items/<item_id>', methods=['PUT'])
def edit_item(item_id):
    """Handles updating item fields (notes, storage, quantity, sizes)."""

    # We expect JSON data from the frontend form submission
    update_data = request.get_json()
    asset_type = update_data.pop('asset_type', None) # Get type, remove from update data

    if not asset_type:
        return jsonify({"error": "Asset Type is required for update."}), 400

    # Clean up and validate fields before saving
    final_update = {}

    if asset_type == 'Costume':
        # Must parse size_inventory back into a list
        size_inventory_json = update_data.get('size_inventory')
        if size_inventory_json:
            try:
                final_update['size_inventory'] = json.loads(size_inventory_json)
            except json.JSONDecodeError:
                return jsonify({"error": "Invalid format for size inventory."}), 400

    elif asset_type in ['Prop', 'Set', 'Tech']:
        # Ensure number (quantity) is an integer if provided
        if 'number' in update_data:
            try:
                final_update['number'] = int(update_data['number'])
                if final_update['number'] < 1:
                    return jsonify({"error": "Quantity (number) must be 1 or greater."}), 400
            except ValueError:
                return jsonify({"error": "Quantity (number) must be an integer."}), 400

    # Update common fields if present
    if 'storage_location' in update_data:
        final_update['storage_location'] = update_data['storage_location']
    if 'notes' in update_data:
        final_update['notes'] = update_data['notes']

    if not final_update:
        return jsonify({"error": "No valid fields provided for update."}), 400

    try:
        doc_ref = db.collection(FIRESTORE_COLLECTION).document(item_id)
        if not doc_ref.get().exists:
            return jsonify({"error": "Item not found."}), 404

        doc_ref.update(final_update)

        return jsonify({"message": f"{asset_type} item {item_id} updated successfully."}), 200

    except Exception as e:
        print(f"Error updating item in Firestore: {e}")
        return jsonify({"error": "Failed to update item (Firestore error). Please check server logs."}), 500


if __name__ == '__main__':
    app.run(debug=True, port=8080)