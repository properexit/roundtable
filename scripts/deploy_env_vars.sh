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
  news-api-key="$NEWS_API_KEY" \
  azure-storage-connection-string="$AZURE_STORAGE_CONNECTION_STRING" \
  site-login-password="$SITE_LOGIN_PASSWORD" \
  site-session-secret="$SITE_SESSION_SECRET" \
  upstox-client-id="$UPSTOX_CLIENT_ID" \
  upstox-client-secret="$UPSTOX_CLIENT_SECRET" \
  upstox-redirect-uri="$UPSTOX_REDIRECT_URI" \
  eval-trigger-secret="$EVAL_TRIGGER_SECRET"

# --set-env-vars forces a new revision, which also gives Azure a fresh
# chance to schedule with the (already-correct) ingress port config instead
# of being stuck on revision 0000001's stale ActivationFailed status.
az containerapp update --name "$APP_NAME" --resource-group "$RESOURCE_GROUP" --set-env-vars \
  AZURE_OPENAI_ENDPOINT=secretref:azure-openai-endpoint \
  AZURE_OPENAI_API_KEY=secretref:azure-openai-api-key \
  AZURE_OPENAI_DEPLOYMENT=secretref:azure-openai-deployment \
  AZURE_LANGUAGE_ENDPOINT=secretref:azure-language-endpoint \
  AZURE_LANGUAGE_KEY=secretref:azure-language-key \
  NEWS_API_KEY=secretref:news-api-key \
  AZURE_STORAGE_CONNECTION_STRING=secretref:azure-storage-connection-string \
  SITE_LOGIN_PASSWORD=secretref:site-login-password \
  SITE_SESSION_SECRET=secretref:site-session-secret \
  UPSTOX_CLIENT_ID=secretref:upstox-client-id \
  UPSTOX_CLIENT_SECRET=secretref:upstox-client-secret \
  UPSTOX_REDIRECT_URI=secretref:upstox-redirect-uri \
  EVAL_TRIGGER_SECRET=secretref:eval-trigger-secret

echo "Done. Give it ~30-60s, then check: az containerapp revision list --name $APP_NAME --resource-group $RESOURCE_GROUP -o table"
