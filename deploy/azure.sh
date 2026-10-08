#!/usr/bin/env bash
# Deploy the Minimum to Azure Container Apps. The learner database is kept safe as a blob copy.
#
# Prerequisites: az CLI logged in (az login). Docker is not required; the image is built in Azure Container Registry.
# Usage:
#   first time:  ANTHROPIC_API_KEY=sk-ant-... MINIMUM_TOKEN=choose-a-long-secret ./deploy/azure.sh
#   later:       ./deploy/azure.sh          (secrets stay as stored; set one to replace it)
# Optional env: RG, LOCATION, APP, ENV_NAME, STORAGE, MINIMUM_MODEL, MINIMUM_LEARNER
#   PERSIST=0   skip the blob copy of the learner database (data is then lost on restart)
#
# Re-running the script updates the app in place.
set -euo pipefail

# Secrets are stored in Azure on the first deploy. On reruns, leave ANTHROPIC_API_KEY and
# MINIMUM_TOKEN unset to keep the stored values, or set one to replace it.
ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-}"
MINIMUM_TOKEN="${MINIMUM_TOKEN:-}"
if [ -n "$ANTHROPIC_API_KEY" ]; then
  case "$ANTHROPIC_API_KEY" in
    sk-ant-??????????*) ;;
    *) echo "ANTHROPIC_API_KEY does not look like a real key (expected sk-ant-... followed by many characters). Get one at https://console.anthropic.com/settings/keys"; exit 1 ;;
  esac
fi
if [ -n "$MINIMUM_TOKEN" ] && { [ "${#MINIMUM_TOKEN}" -lt 16 ] || [ "$MINIMUM_TOKEN" = "..." ]; }; then
  echo "MINIMUM_TOKEN must be a real secret of at least 16 characters. Make one with: export MINIMUM_TOKEN=\$(openssl rand -hex 24)"; exit 1
fi

RG="${RG:-minimum-rg}"
LOCATION="${LOCATION:-eastus2}"  # if a region reports AKSCapacityHeavyUsage, pick another: westus2, centralus, westeurope
APP="${APP:-minimum}"
ENV_NAME="${ENV_NAME:-minimum-env}"
STORAGE="${STORAGE:-minimum$(echo "$RG" | tr -dc 'a-z0-9' | cut -c1-8)$RANDOM}"
SHARE="learnerdata"  # blob container name
MODEL="${MINIMUM_MODEL:-claude-opus-5-5}"
PERSIST="${PERSIST:-1}"
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

if [ "$PERSIST" = "1" ]; then
echo "== storage for the learner database (blob copy, restored on start, saved after every write)"
EXISTING=$(az storage account list -g "$RG" --query "[?starts_with(name,'minimum')].name | [0]" -o tsv)
if [ -n "$EXISTING" ]; then STORAGE="$EXISTING"; else
  az storage account create -g "$RG" -n "$STORAGE" -l "$LOCATION" --sku Standard_LRS --kind StorageV2 --only-show-errors >/dev/null
fi
STORAGE_CONN=$(az storage account show-connection-string -g "$RG" -n "$STORAGE" --query connectionString -o tsv)
az storage container create --name "$SHARE" --connection-string "$STORAGE_CONN" --only-show-errors >/dev/null
echo "   using blob container $SHARE on storage account $STORAGE"
else
  STORAGE_CONN=""
  echo "== storage: PERSIST=0, no blob copy (data is lost on restart)"
fi

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

BUILD=$(git rev-parse --short HEAD 2>/dev/null || echo manual)
TAG="$(date +%Y%m%d%H%M%S)-$BUILD"
IMAGE="$ACR_SERVER/minimum:$TAG"
echo "== building $IMAGE from commit $BUILD (a few minutes; build output follows)"
az acr build -r "$ACR" -t "minimum:$TAG" --build-arg "BUILD=$BUILD" .
if ! az acr repository show-tags -n "$ACR" --repository minimum -o tsv | grep -qx "$TAG"; then
  echo "BUILD FAILED: tag $TAG is not in the registry. Not deploying. Read the build output above."; exit 1
fi

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

echo "== secrets"
HAVE=$(az containerapp secret list -g "$RG" -n "$APP" --query "[].name" -o tsv 2>/dev/null | tr '\n' ' ')
SECRETS=()
if [ -n "$ANTHROPIC_API_KEY" ]; then SECRETS+=("anthropic-api-key=$ANTHROPIC_API_KEY"); fi
if [ -n "$MINIMUM_TOKEN" ]; then SECRETS+=("minimum-token=$MINIMUM_TOKEN"); fi
if [ -n "$STORAGE_CONN" ]; then SECRETS+=("storage-connection=$STORAGE_CONN"); fi
case " $HAVE " in *" anthropic-api-key "*) ;; *) [ -n "$ANTHROPIC_API_KEY" ] || { echo "First deploy needs ANTHROPIC_API_KEY set."; exit 1; } ;; esac
case " $HAVE " in *" minimum-token "*) ;; *) [ -n "$MINIMUM_TOKEN" ] || { echo "First deploy needs MINIMUM_TOKEN set (export MINIMUM_TOKEN=\$(openssl rand -hex 24))."; exit 1; } ;; esac
if [ "${#SECRETS[@]}" -gt 0 ]; then
  az containerapp secret set -g "$RG" -n "$APP" --secrets "${SECRETS[@]}" --only-show-errors >/dev/null
fi
[ -n "$ANTHROPIC_API_KEY" ] || echo "   keeping the stored API key"
[ -n "$MINIMUM_TOKEN" ] || echo "   keeping the stored access token"
ENVVARS=("ANTHROPIC_API_KEY=secretref:anthropic-api-key" "MINIMUM_TOKEN=secretref:minimum-token")
if [ -n "$STORAGE_CONN" ]; then
  ENVVARS+=("MINIMUM_BLOB_CONNECTION=secretref:storage-connection" "MINIMUM_BLOB_CONTAINER=$SHARE")
fi
az containerapp update -g "$RG" -n "$APP" --set-env-vars "${ENVVARS[@]}" --only-show-errors >/dev/null

# Remove any file-share volume left from earlier versions of this script; the blob copy replaces it.
TMP=$(mktemp --suffix=.json)
az containerapp show -g "$RG" -n "$APP" -o json > "$TMP"
if python3 - "$TMP" <<'PY'
import json, sys
doc = json.load(open(sys.argv[1]))
tpl = doc["properties"]["template"]
had = bool(tpl.get("volumes")) or any(c.get("volumeMounts") for c in tpl["containers"])
tpl["volumes"] = []
for c in tpl["containers"]:
    c["volumeMounts"] = []
json.dump(doc, open(sys.argv[1], "w"))
sys.exit(0 if had else 1)
PY
then
  echo "   removing the old file-share volume"
  az containerapp update -g "$RG" -n "$APP" --yaml "$TMP" --only-show-errors >/dev/null
fi
rm -f "$TMP"

echo "== waiting for the new revision to become ready"
LATEST=""; READY=""
for i in $(seq 1 30); do
  LATEST=$(az containerapp show -g "$RG" -n "$APP" --only-show-errors --query properties.latestRevisionName -o tsv || true)
  READY=$(az containerapp show -g "$RG" -n "$APP" --only-show-errors --query properties.latestReadyRevisionName -o tsv || true)
  if [ -n "$LATEST" ] && [ "$LATEST" = "$READY" ]; then break; fi
  printf '.'
  sleep 10
done
echo
echo "   latest revision: $LATEST   ready revision: $READY"
if [ "$LATEST" != "$READY" ]; then
  echo "DEPLOY FAILED: revision $LATEST never became ready; $READY is still serving. Recent system log:"
  az containerapp logs show -g "$RG" -n "$APP" --type system --tail 40 --only-show-errors 2>/dev/null || true
  echo "Recent console log (the app's own output):"
  az containerapp logs show -g "$RG" -n "$APP" --type console --tail 40 --only-show-errors 2>/dev/null || true
  exit 1
fi

URL="https://$(az containerapp show -g "$RG" -n "$APP" --query properties.configuration.ingress.fqdn -o tsv)"
echo
if [ -n "$MINIMUM_TOKEN" ]; then echo "Deployed commit $BUILD. Open: $URL/?token=$MINIMUM_TOKEN"; else echo "Deployed commit $BUILD. Open: $URL  (your existing token still works)"; fi
echo "Check:         curl -s $URL/healthz   (the build field must read $BUILD)"
echo "Health:        $URL/healthz"
echo "Logs:          az containerapp logs show -g $RG -n $APP --follow"
