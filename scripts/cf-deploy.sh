#!/bin/bash
# Trigger/check Cloudflare Pages deployment via API
# Usage: ./scripts/cf-deploy.sh [--status]

source /home/darren/.env.cloudflare 2>/dev/null
TOKEN="${CLOUDFLARE_API_TOKEN}"
ACCOUNT_ID="0f25318d292c7b6fcd1eb5502ff85b8a"
PROJECT="mcas"

if [ -z "$TOKEN" ]; then
  echo "Error: CLOUDFLARE_API_TOKEN not set"
  exit 1
fi

if [ "$1" = "--status" ]; then
  curl -s "https://api.cloudflare.com/client/v4/accounts/${ACCOUNT_ID}/pages/projects/${PROJECT}/deployments" \
    -H "Authorization: Bearer ${TOKEN}" | python3 -c "
import json, sys
data = json.load(sys.stdin)
for d in data['result'][:5]:
    commit = d.get('deployment_trigger',{}).get('metadata',{}).get('commit_hash','?')[:7]
    msg = d.get('deployment_trigger',{}).get('metadata',{}).get('commit_message','?')[:50]
    status = d.get('latest_stage',{}).get('status','?')
    url = d.get('url','?')
    print(f'{commit}  {status:10s}  {url}')
    print(f'  {msg}')
"
  exit 0
fi

echo "Pushing to trigger Cloudflare Pages build..."
git push origin main 2>&1
echo ""
echo "Checking deployment status in 5s..."
sleep 5
curl -s "https://api.cloudflare.com/client/v4/accounts/${ACCOUNT_ID}/pages/projects/${PROJECT}/deployments" \
  -H "Authorization: Bearer ${TOKEN}" | python3 -c "
import json, sys
d = json.load(sys.stdin)['result'][0]
commit = d.get('deployment_trigger',{}).get('metadata',{}).get('commit_hash','?')[:7]
status = d.get('latest_stage',{}).get('status','?')
name = d.get('latest_stage',{}).get('name','?')
print(f'Latest: {commit} -- {name}: {status}')
print(f'URL: {d.get(\"url\",\"?\")}')"
