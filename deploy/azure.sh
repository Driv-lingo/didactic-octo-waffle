#!/usr/bin/env bash
# Deploy the Minimum to Azure Container Apps with a persistent Azure Files share.
#
# Prerequisites: az CLI logged in (az login), Docker not required (ACA builds from source).
# Usage:
#   ANTHROPIC_API_KEY=sk-ant-... MINIMUM_TOKEN=choose-a-long-secret ./deploy/azure.sh
# Optional env: RG, LOCATION, APP, ENV_NAME, STORAGE, MINIMUM_MODEL, MINIMUM_LEARNER
#
# Re-running the script updates the app in place.
set -euo pipefail

: "${ANTHROPIC_API_KEY:?set ANTHROPIC_API_KEY}"
: "${MINIMUM_TOKEN:?set MINIMUM_TOKEN (the browser access token)}"
case "$ANTHROPIC_API_KEY" in
  sk-ant-??????????*) ;;
  *) echo "ANTHROPIC_API_KEY does not look like a real key (expected sk-ant-... followed by many characters). Get one at https://console.anthropic.com/settings/keys"; exit 1 ;;
esac
if [ "${#MINIMUM_TOKEN}" -lt 16 ] || [ "$MINIMUM_TOKEN" = "..." ]; then
  echo "MINIMUM_TOKEN must be a real secret of at least 16 characters. Make one with: export MINIMUM_TOKEN=\$(openssl rand -hex 24)"; exit 1
fi

RG="${RG:-minimum-rg}"
LOCATION="${LOCATION:-eastus2}"  # if a region reports AKSCapacityHeavyUsage, pick another: westus2, centralus, westeurope
APP="${APP:-minimum}"
ENV_NAME="${ENV_NAME:-minimum-env}"
STORAGE="${STORAGE:-minimum$(echo "$RG" | tr -dc 'a-z0-9' | cut -c1-8)$RANDOM}"
SHARE="learnerdata"
MODEL="${MINIMUM_MODEL:-claude-opus-5-5}"
LEARNER="${MINIMUM_LEARNER:-me}"

az extension add --name containerapp --upgrade --only-show-errors >/dev/null

echo "== registering resource providers (a fresh subscription reports 'SubscriptionNotFound' until these are done)"
for ns in Microsoft.App Microsoft.OperationalInsights Microsoft.Storage Microsoft.ContainerRegistry; do
  if [ "$(az provider show -n "$ns" --query registrationState -o tsv 2>/dev/null)" != "Registered" ]; then
    az provider register --namespace "$ns" --wait --only-show-errors >/dev/null
  fi
done

echo "== resource group $RG (resources go to $LOCATION)"
if [ "$(az group exists -n "$RG")" != "true" ]; then
  az group create -n "$RG" -l "$LOCATION" --only-show-errors >/dev/null
fi

echo "== container apps environment $ENV_NAME"
ENV_STATE=$(az containerapp env show -g "$RG" -n "$ENV_NAME" --query properties.provisioningState -o tsv 2>/dev/null || true)
if [ -n "$ENV_STATE" ] && [ "$ENV_STATE" != "Succeeded" ]; then
  echo "   existing environment is in state '$ENV_STATE'; deleting it and recreating in $LOCATION"
  az containerapp env delete -g "$RG" -n "$ENV_NAME" --yes --only-show-errors >/dev/null || true
  ENV_STATE=""
fi
if [ -z "$ENV_STATE" ]; then
  az containerapp env create -g "$RG" -n "$ENV_NAME" -l "$LOCATION" --only-show-errors >/dev/null
else
  LOCATION=$(az containerapp env show -g "$RG" -n "$ENV_NAME" --query location -o tsv | tr -d ' ' | tr '[:upper:]' '[:lower:]')
  echo "   reusing existing environment in $LOCATION"
fi

echo "== storage for the learner database"
EXISTING=$(az storage account list -g "$RG" --query "[?starts_with(name,'minimum')].name | [0]" -o tsv)
if [ -n "$EXISTING" ]; then STORAGE="$EXISTING"; else
  az storage account create -g "$RG" -n "$STORAGE" -l "$LOCATION" --sku Standard_LRS --kind StorageV2 --only-show-errors >/dev/null
fi
KEY=$(az storage account keys list -g "$RG" -n "$STORAGE" --query "[0].value" -o tsv)
az storage share-rm create --storage-account "$STORAGE" --name "$SHARE" --quota 5 --only-show-errors >/dev/null 2>&1 || true
az containerapp env storage set -g "$RG" -n "$ENV_NAME" --storage-name "$SHARE" \
  --azure-file-account-name "$STORAGE" --azure-file-account-key "$KEY" --azure-file-share-name "$SHARE" \
  --access-mode ReadWrite --only-show-errors >/dev/null

echo "== build and deploy the app from source (5-15 minutes; build output follows)"
az containerapp up -g "$RG" -n "$APP" --environment "$ENV_NAME" --source . --ingress external --target-port 8000 \
  --env-vars "MINIMUM_DB=/data/learner.db" "MINIMUM_MODEL=$MODEL" "MINIMUM_LEARNER=$LEARNER"

echo "== secrets and volume"
az containerapp secret set -g "$RG" -n "$APP" --secrets "anthropic-api-key=$ANTHROPIC_API_KEY" "minimum-token=$MINIMUM_TOKEN" --only-show-errors >/dev/null
az containerapp update -g "$RG" -n "$APP" --min-replicas 1 --max-replicas 1 \
  --set-env-vars "ANTHROPIC_API_KEY=secretref:anthropic-api-key" "MINIMUM_TOKEN=secretref:minimum-token" --only-show-errors >/dev/null

# Mount the share at /data. The CLI exposes volumes only through a YAML/JSON app definition,
# so patch the app definition with the standard library only (JSON is valid YAML).
TMP=$(mktemp --suffix=.json)
az containerapp show -g "$RG" -n "$APP" -o json > "$TMP"
python3 - "$TMP" "$SHARE" <<'PY'
import json, sys
path, share = sys.argv[1], sys.argv[2]
doc = json.load(open(path))
tpl = doc["properties"]["template"]
tpl["volumes"] = [{"name": "data", "storageType": "AzureFile", "storageName": share}]
for c in tpl["containers"]:
    c["volumeMounts"] = [{"volumeName": "data", "mountPath": "/data"}]
json.dump(doc, open(path, "w"))
PY
az containerapp update -g "$RG" -n "$APP" --yaml "$TMP" --only-show-errors >/dev/null
rm -f "$TMP"

URL="https://$(az containerapp show -g "$RG" -n "$APP" --query properties.configuration.ingress.fqdn -o tsv)"
echo
echo "Deployed. Open: $URL/?token=$MINIMUM_TOKEN"
echo "Health:        $URL/healthz"
echo "Logs:          az containerapp logs show -g $RG -n $APP --follow"
