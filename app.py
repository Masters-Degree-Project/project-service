from flask import Flask, request, jsonify
from functools import wraps
import jwt
from pymongo import MongoClient
from bson import ObjectId
import os
from dotenv import load_dotenv
from slugify import slugify

# Load environment variables
load_dotenv()

app = Flask(__name__)

# MongoDB connection
client = MongoClient(os.getenv('MONGODB_URI', 'mongodb://localhost:27017/'))
db = client[os.getenv('DB_NAME', 'project_service')]
projects_collection = db.projects

# JWT Configuration
JWT_SECRET = os.getenv('JWT_SECRET')

def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get('Authorization')
        
        if not token:
            return jsonify({'message': 'Token is missing'}), 401

        try:
            # Remove 'Bearer ' prefix if present
            if token.startswith('Bearer '):
                token = token.split(' ')[1]
            
            # Verify the token and get payload
            payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
            # Add payload to flask.g or request for use in routes
            request.token_payload = payload
        except jwt.ExpiredSignatureError:
            return jsonify({'message': 'Token has expired'}), 401
        except jwt.InvalidTokenError:
            return jsonify({'message': 'Invalid token'}), 401

        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not hasattr(request, 'token_payload') or request.token_payload.get('role') != 'admin':
            return jsonify({'message': 'Admin privileges required'}), 403
        return f(*args, **kwargs)
    return decorated

# Routes
@app.route('/projects', methods=['GET'])
@token_required
def get_projects():
    projects = list(projects_collection.find())
    for project in projects:
        project['_id'] = str(project['_id'])
    return jsonify(projects)

@app.route('/projects', methods=['POST'])
@token_required
@admin_required
def create_project():
    data = request.get_json()
    
    if not data or not data.get('name'):
        return jsonify({'message': 'Name is required'}), 400
    
    # Generate slug from name
    slug = slugify(data['name'])
    
    # Check if project with same name or slug already exists
    existing_project = projects_collection.find_one({
        '$or': [
            {'name': data['name']},
            {'slug': slug}
        ]
    })
    
    if existing_project:
        if existing_project['name'] == data['name']:
            return jsonify({'message': 'A project with this name already exists'}), 400
        else:
            return jsonify({'message': 'A project with this slug already exists'}), 400
    
    project = {
        'name': data['name'],
        'description': data.get('description', ''),
        'slug': slug
    }
    
    result = projects_collection.insert_one(project)
    project['_id'] = str(result.inserted_id)
    
    return jsonify(project), 201

@app.route('/project/<id>', methods=['GET'])
@token_required
def get_project(id):
    try:
        project = projects_collection.find_one({'_id': ObjectId(id)})
        if project:
            project['_id'] = str(project['_id'])
            return jsonify(project)
        return jsonify({'message': 'Project not found'}), 404
    except:
        return jsonify({'message': 'Invalid project ID'}), 400

if __name__ == '__main__':
    if not JWT_SECRET:
        raise ValueError("JWT_SECRET environment variable is required")
    app.run(debug=True, host='0.0.0.0', port=os.getenv('PORT', 8000))