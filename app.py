from flask import Flask, request, jsonify
from functools import wraps
import jwt
from pymongo import MongoClient
from bson import ObjectId
import os
import atexit
import socket
import consul
from dotenv import load_dotenv
from slugify import slugify

# Load environment variables
load_dotenv()

app = Flask(__name__)

# MongoDB connection
db_host = os.getenv('DB_HOST')
db_port = os.getenv('DB_PORT')
db_name = os.getenv('DB_NAME')

print(f'db_host: {db_host}')
print(f'db_port: {db_port}')
print(f'db_name: {db_name}')

try:
    client = MongoClient(f'mongodb://{db_host}:{db_port}', serverSelectionTimeoutMS=5000)
    print(f'mongo connection string >> mongodb://{db_host}:{db_port}/')
    # Test the connection
    client.admin.command('ping')
    db = client[db_name]
    projects_collection = db.projects
except Exception as e:
    print(f"Failed to connect to MongoDB: {str(e)}")
    raise

# JWT Configuration
JWT_SECRET = os.getenv('JWT_SECRET')

# Consul Configuration
CONSUL_HOST = os.getenv('CONSUL_HOST', 'localhost')
CONSUL_PORT = int(os.getenv('CONSUL_PORT', 8500))
SERVICE_NAME = os.getenv('SERVICE_NAME', 'project-service')
SERVICE_PORT = int(os.getenv('SERVICE_PORT', 8000))
SERVICE_HOST = os.getenv('SERVICE_HOST', socket.gethostname())
SERVICE_ID = os.getenv('SERVICE_ID', 'project-service-dev-1')

# Initialize Consul client
consul_client = consul.Consul(host=CONSUL_HOST, port=CONSUL_PORT)

def register_service():
    """Register service with Consul"""
    consul_client.agent.service.register(
        name=SERVICE_NAME,
        service_id=SERVICE_ID,
        address=SERVICE_HOST,
        port=SERVICE_PORT,
        tags=[
            'project-service', 
            'microservice',
            'traefik.enable=true',
            f'traefik.http.routers.{SERVICE_NAME}-router.rule=Headers(`X-Service`, `{SERVICE_NAME}`)',
            f'traefik.http.routers.{SERVICE_NAME}-router.service={SERVICE_NAME}',
            f'traefik.http.routers.{SERVICE_NAME}-router.entryPoints=web',
            f'traefik.http.services.{SERVICE_NAME}.loadBalancer.server.port={SERVICE_PORT}'
        ],
        check={
            'http': f'http://{SERVICE_HOST}:{SERVICE_PORT}/health',
            'interval': '10s',
            'timeout': '5s'
        }
    )
    return SERVICE_ID

def deregister_service(service_id):
    """Deregister service from Consul"""
    consul_client.agent.service.deregister(service_id)

# Health check endpoint
@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({'status': 'healthy'}), 200

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
@app.route('/api/v1/projects', methods=['GET'])
@token_required
def get_projects():
    projects = list(projects_collection.find())
    for project in projects:
        project['_id'] = str(project['_id'])
    return jsonify(projects)

@app.route('/api/v1/projects', methods=['POST'])
@token_required
@admin_required
def create_project():
    try:
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
    except Exception as e:
        app.logger.error(f"Error creating project: {str(e)}")
        return jsonify({'message': 'An error occurred while creating the project'}), 500

@app.route('/api/v1/project/<id>', methods=['GET'])
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
    
    # Register service with Consul
    service_id = register_service()
    
    # Register deregistration function to run on shutdown
    atexit.register(deregister_service, service_id)
    
    app.run(debug=True, host='0.0.0.0', port=SERVICE_PORT)