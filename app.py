import os
import base64
import requests
from flask import Flask, render_template, request, redirect, url_for
from firebase_admin import credentials, initialize_app, firestore
from werkzeug.utils import secure_filename

# Configuration
GITHUB_API_URL = "https://api.github.com/repos"
TOKEN = os.environ.get('GITHUB_TOKEN')
OWNER = os.environ.get('PHOTO_REPO_OWNER')
REPO = os.environ.get('PHOTO_REPO_NAME')
GITHUB_PAGES_BASE_URL = os.environ.get('GITHUB_PAGES_BASE_URL')

# Initialization
app = Flask(__name__)

try:
    cred = credentials.Certificate('serviceAccountKey.json')
    initialize_app(cred)
    db = firestore.client()
except Exception as e:
    print(f"FATAL: Firebase Init Error: {e}")

# GITHUB Upload Function
def upload_photo_to_github(file):
    filename = secure_filename(file.filename)

    content_bytes = file.read()
    encoded_content = base64.b64encode(content_bytes).decode('utf-8')

    path = f"images/{filename}"
    upload_url = f"{GITHUB_API_URL}/{OWNER}/{REPO}/contents/{path}"
    headers = {
        "Authorization": f"token {TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    payload = {
        "message": f"Upload photo for new gear: {filename}",
        "content": encoded_content,
        "branch": "main"
    }

    response = requests.put(upload_url, headers=headers, json=payload)

    if response.status_code in [200, 201]: # 200 = Updated, 201 = Created
        # The image URL for firestore
        return f"{GITHUB_PAGES_BASE_URL}/{path}"
    else:
        print(f"GITHUB Upload Failed ({response.status_code}): {response.text}")
        return None

# Flask Routes
@app.route('/', methods=['GET', 'POST'])
def inventory_list():
    gear_ref = db.collection('gear')

    if request.method == 'POST':
        name = request.form.get('name')
        asset_id = request.form.get('asset_id')
        photo_file = request.files.get('photo')

        photo_url = None
        if photo_file and photo_file.filename:
            # Upload to GITHUB
            photo_url = upload_photo_to_github(photo_file)

        gear_ref.document(asset_id).set({
            'name': name,
            'status': 'Available',
            'photo_url': photo_url
        })
        return redirect(url_for('inventory_list'))

    items = [doc.to_dict() for doc in gear_ref.stream()]
    return render_template('index.html', items=items)

if __name__ == '__main__':
    app.run(debug=True)