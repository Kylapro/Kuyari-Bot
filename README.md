# Kuyari-Bot

## Google Application Default Credentials

The bot can authenticate with Google APIs by supplying Application Default Credentials (ADC).

### Using your Google user account
1. Install the [Google Cloud CLI](https://cloud.google.com/sdk/docs/install).
2. Run `gcloud auth application-default login` and complete the sign‑in flow.
3. The credentials are stored in your local ADC file and discovered automatically.

### Using a service account key
1. In the Google Cloud console, create a service account and grant the roles the bot needs.
2. Generate a JSON key for the service account and download it.
3. Set `google_credentials_file` in `config.yaml` to the path of that JSON key or set the environment variable `GOOGLE_APPLICATION_CREDENTIALS` to that path before running the bot.
4. Keep the key secure and rotate it regularly.

The bot will load the file at startup and set `GOOGLE_APPLICATION_CREDENTIALS` so client libraries can authenticate automatically.
