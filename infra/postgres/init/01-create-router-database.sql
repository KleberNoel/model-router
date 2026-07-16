-- Fresh Compose installs keep Open WebUI and model-router state separate.
-- Cloud deployments should use a dedicated database role and secret instead.
SELECT 'CREATE DATABASE model_router'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'model_router')\gexec
