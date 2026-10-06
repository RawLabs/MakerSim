"""Local browser QA transport: exercise the real API without a network server."""
import base64
import json
import sys
from fastapi.testclient import TestClient
from backend.app import app

with TestClient(app, base_url='https://makersim.local') as client:
    for line in sys.stdin:
        try:
            request=json.loads(line)
            response=client.request(request['method'],request['path'],headers=request.get('headers',{}),content=base64.b64decode(request.get('body','')))
            result={'id':request['id'],'status':response.status_code,'body':base64.b64encode(response.content).decode(),'content_type':response.headers.get('content-type','application/json')}
        except Exception as error:
            result={'id':request.get('id'),'error':str(error)}
        print(json.dumps(result),flush=True)
