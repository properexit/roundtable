#!/usr/bin/env bash
# One-time (or repeatable) sync of local .env values into the Azure
# Container App as secrets + env vars referencing them. Reads directly from
# .env so you never have to retype keys into a command or the portal by
# hand -- run this from the repo root, in your own terminal where `az` is
# installed and you're logged in.
set -euo pipefail

APP_NAME="roundtable-api"
RESOURCE_GROUP="rg-roundtable"

source .env

az containerapp secret set --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" --secrets \
  azure-openai-endpoint="$AZURE_OPENAI_ENDPOINT" \
  azure-openai-api-key="$AZURE_OPENAI_API_KEY" \
  azure-openai-deployment="$AZURE_OPENAI_DEPLOYMENT" \
  azure-language-endpoint="$AZURE_LANGUAGE_ENDPOINT" \
  azure-language-key="$AZURE_LANGUAGE_KEY" \
  news-api-key="$NEWS_API_KEY"

# --set-env-vars forces a new revision, which also gives Azure a fresh
# chance to schedule with the (already-correct) ingress port config instead
# of being stuck on revision 0000001's stale ActivationFailed status.
az containerapp update --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" --set-env-vars \
  AZURE_OPENAI_ENDPOINT=secretref:azure-openai-endpoint \
  AZURE_OPENAI_API_KEY=secretref:azure-openai-api-key \
  AZURE_OPENAI_DEPLOYMENT=secretref:azure-openai-deployment \
  AZURE_LANGUAGE_ENDPOINT=secretref:azure-language-endpoint \
  AZURE_LANGUAGE_KEY=secretref:azure-language-key \
  NEWS_API_KEY=secretref:news-api-key

echo "Done. Give it ~30-60s, then check: az containerapp revision list --name $APP_NAME --resource-group $RESOURCE_GROUP -o table"
