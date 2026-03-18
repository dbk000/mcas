import json
import sys
import os

# Add Backend to sys.path
sys.path.append(os.path.join(os.getcwd(), 'Backend'))

from api.app.main import app

# Generate OpenAPI schema
schema = app.openapi()

# Save to Frontend/src/mocks/openapi.json
output_path = os.path.join(os.getcwd(), 'Frontend/src/mocks/openapi.json')
with open(output_path, 'w') as f:
    json.dump(schema, f, indent=2)

print(f"OpenAPI schema saved to {output_path}")
