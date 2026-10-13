# Deploying QCH to Streamlit Community Cloud

## Application

- Entry point: `app.py`
- Public pages include Multi-Version Circuit Explorer, Ask QCH with Natural Language, and Version Evolution Explorer.
- Runtime dependencies are defined in `requirements.txt`.
- The application uses the Git-tracked SQLite database at `data/qch_ecdsa_p17.sqlite3`.

## Deploy

1. Push the intended release branch to GitHub.
2. Open https://share.streamlit.io and create an app from that repository and branch.
3. Set **Main file path** to `app.py`.
4. Select Python 3.11 or Python 3.12.
5. In **Advanced settings -> Secrets**, add:

   ```toml
   QCH_LLM_ENABLED = "false"