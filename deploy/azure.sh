#!/usr/bin/env bash
# Deploy the Minimum to Azure Container Apps with a persistent Azure Files share.
#
# Prerequisites: az CLI logged in (az login). Docker is not required; the image is built in Azure Container Registry.
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

echo "== container registry"
ACR=$(az acr list -g "$RG" --query "[0].name" -o tsv)
if [ -z "$ACR" ]; then
  ACR="minimumacr$RANDOM$RANDOM"
  az acr create -g "$RG" -n "$ACR" -l "$LOCATION" --sku Basic --admin-enabled true --only-show-errors >/dev/null
fi
az acr update -n "$ACR" --admin-enabled true --only-show-errors >/dev/null
ACR_SERVER=$(az acr show -n "$ACR" --query loginServer -o tsv)
ACR_USER=$(az acr credential show -n "$ACR" --query username -o tsv)
ACR_PASS=$(az acr credential show -n "$ACR" --query "passwords[0].value" -o tsv)

TAG=$(date +%Y%m%d%H%M%S)
IMAGE="$ACR_SERVER/minimum:$TAG"
echo "== building $IMAGE in the cloud (a few minutes; build output follows)"
az acr build -r "$ACR" -t "minimum:$TAG" . | grep -Ev '^\s*$|Pushed$|Preparing$|Waiting$|Retrying' || true

echo "== deploying the app"
APP_STATE=$(az containerapp show -g "$RG" -n "$APP" --query properties.provisioningState -o tsv 2>/dev/null || true)
if [ -n "$APP_STATE" ] && [ "$APP_STATE" != "Succeeded" ]; then
  echo "   existing app is in state '$APP_STATE'; deleting it and recreating"
  az containerapp delete -g "$RG" -n "$APP" --yes --only-show-errors >/dev/null || true
  APP_STATE=""
fi
if [ -z "$APP_STATE" ]; then
  az containerapp create -g "$RG" -n "$APP" --environment "$ENV_NAME" --image "$IMAGE" \
    --registry-server "$ACR_SERVER" --registry-username "$ACR_USER" --registry-password "$ACR_PASS" \
    --ingress external --target-port 8000 --min-replicas 1 --max-replicas 1 --cpu 0.5 --memory 1.0Gi \
    --env-vars "MINIMUM_DB=/data/learner.db" "MINIMUM_MODEL=$MODEL" "MINIMUM_LEARNER=$LEARNER" \
    --only-show-errors >/dev/null
else
  az containerapp registry set -g "$RG" -n "$APP" --server "$ACR_SERVER" --username "$ACR_USER" --password "$ACR_PASS" --only-show-errors >/dev/null
  az containerapp update -g "$RG" -n "$APP" --image "$IMAGE" \
    --set-env-vars "MINIMUM_DB=/data/learner.db" "MINIMUM_MODEL=$MODEL" "MINIMUM_LEARNER=$LEARNER" \
    --only-show-errors >/dev/null
fi

echo "== secrets and volume"
az containerapp secret set -g "$RG" -n "$APP" --secrets "anthropic-api-key=$ANTHROPIC_API_KEY" "minimum-token=$MINIMUM_TOKEN" --only-show-errors >/dev/null
az containerapp update -g "$RG" -n "$APP" \
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
