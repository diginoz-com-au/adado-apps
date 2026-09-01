-- Auto-create all databases needed by AdaDo apps on first postgres start
CREATE DATABASE IF NOT EXISTS plane;
CREATE DATABASE IF NOT EXISTS vaultwarden;
GRANT ALL PRIVILEGES ON DATABASE plane TO adado;
GRANT ALL PRIVILEGES ON DATABASE vaultwarden TO adado;
