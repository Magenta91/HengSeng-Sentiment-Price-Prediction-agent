# Deployment Guide

Production deployment for Heng Seng Sentiment & Price Prediction Agent on Render (backend + PostgreSQL) and Vercel (frontend).

## Prerequisites

- GitHub repository: https://github.com/Magenta91/HengSeng-Sentiment-Price-Prediction-agent
- Render account (for backend + database)
- Vercel account (for frontend)
- API credentials: APIFY_API_TOKEN, EXA_API_KEY, GEMINI_API_KEY

## Security Status

✅ **All credentials sanitized from repository**
- `.env.example` contains only placeholders
- `.env` is gitignored (never committed)
- No API keys in source code or configuration files

⚠️ **IMPORTANT**: The credentials that were previously in `.env.example` have been exposed and should be **rotated immediately**:
- APIFY_API_TOKEN (starts with `apify_api_tSK...`)
- EXA_API_KEY (starts with `c97b2567...`)
- GEMINI_API_KEY (starts with `AIzaSyDTB...`)

**Action Required**: Generate new API keys from provider dashboards before deployment.

## Render Backend Deployment

### Step 1: Provision PostgreSQL Database

1. Go to Render Dashboard → New → PostgreSQL
2. Configure:
   - **Name**: sentiment-db
   - **Database**: sentiment_agent
   - **User**: sentiment_user
   - **Plan**: Free (or paid for production persistence)
3. Wait for provisioning
4. Copy the **Internal Database URL** from dashboard

### Step 2: Deploy FastAPI Service

1. Go to Render Dashboard → New → Web Service
2. Connect GitHub repository: `Magenta91/HengSeng-Sentiment-Price-Prediction-agent`
3. Configure:
   - **Name**: heng-seng-sentiment-api
   - **Runtime**: Python 3
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
   - **Plan**: Free (or paid for production)

### Step 3: Set Environment Variables

In Render dashboard → Environment:

```bash
APP_ENV=production
LOG_LEVEL=INFO
STORAGE_BACKEND=postgres
DATABASE_URL=[paste Internal Database URL from Step 1]
APIFY_API_TOKEN=[your NEW rotated token]
EXA_API_KEY=[your NEW rotated key]
GEMINI_API_KEY=[your NEW rotated key]
GEMINI_MODEL=models/gemini-2.5-flash
FINBERT_ENABLED=false
MARKET_DATA_PROVIDER=yfinance
MODEL_PATH=model_artifacts/random_forest_model.joblib
MODEL_METADATA_PATH=model_artifacts/model_metadata.json
DEFAULT_LOOKBACK_DAYS=10
CORS_ORIGINS=[will be set after Vercel deployment]
```

### Step 4: Initialize Database

After first deploy, run once:

```bash
# SSH into Render shell or use Render API
python -m scripts.init_db
```

### Step 5: Verify Backend

- Health check: `https://your-app.onrender.com/health`
- API docs: `https://your-app.onrender.com/docs`

## Vercel Frontend Deployment

### Step 1: Import Repository

1. Go to Vercel Dashboard → New Project
2. Import GitHub repository: `Magenta91/HengSeng-Sentiment-Price-Prediction-agent`
3. Configure:
   - **Framework Preset**: Vite
   - **Root Directory**: `frontend`
   - **Build Command**: `npm run build`
   - **Output Directory**: `dist`

### Step 2: Set Environment Variable

In Vercel → Settings → Environment Variables:

```bash
VITE_API_BASE_URL=[your Render backend URL from Step 5 above]
```

Example: `VITE_API_BASE_URL=https://heng-seng-sentiment-api.onrender.com`

### Step 3: Redeploy

Click "Redeploy" to apply the environment variable.

### Step 4: Update Backend CORS

Go back to Render → Environment and update:

```bash
CORS_ORIGINS=[your Vercel deployment URL]
```

Example: `CORS_ORIGINS=https://your-app.vercel.app`

### Step 5: Verify Frontend

- Open Vercel URL: `https://your-app.vercel.app`
- Select exchange (HKEX or KRX)
- Select "Demo (synthetic)" mode
- Click "Run pipeline"
- Verify dashboard displays rankings and asset details

## Post-Deployment Verification

### Backend Health Check

```bash
curl https://your-app.onrender.com/health
```

Expected response:
```json
{
  "status": "ok",
  "model_loaded": true,
  "storage": "postgres"
}
```

### Database Persistence Test

1. Run demo pipeline through dashboard
2. Restart backend service in Render
3. Refresh dashboard - data should persist

### API Integration Test

```bash
# Get exchanges
curl https://your-app.onrender.com/api/assets

# Start demo run
curl -X POST https://your-app.onrender.com/api/pipeline/runs \
  -H "Content-Type: application/json" \
  -d '{"exchange":"HKEX","mode":"demo"}'

# Get run status (use run_id from response)
curl https://your-app.onrender.com/api/pipeline/runs/{run_id}

# Get latest report
curl https://your-app.onrender.com/api/reports/latest?exchange=HKEX&mode=demo
```

## Production Considerations

### Free Tier Limitations

**Render Free:**
- Backend spins down after 15 min inactivity (cold starts ~30s)
- PostgreSQL free tier: Limited storage, 90-day retention
- 750 hours/month compute

**Vercel Free:**
- Generous bandwidth and build minutes
- Suitable for production

**Recommendations for Production:**
- Upgrade Render to paid plan ($7/month minimum)
- Upgrade PostgreSQL to paid for persistence
- Monitor API quota usage (Gemini: 20 requests/day free tier)

### Security Best Practices

1. ✅ Rotate all exposed API credentials
2. ✅ Use Render environment secrets (not hardcoded)
3. ✅ Restrict CORS to actual frontend domain
4. ✅ Enable Render HTTPS (automatic)
5. ✅ Monitor access logs for anomalies
6. ⚠️ Consider rate limiting for production

### Model Artifact

- Model file: 0.23MB (included in Git)
- Loads once at startup
- Random Forest (market-only, 18 features)
- Version: 20261110-0938
- Performance: 51.03% directional accuracy (baseline)

### Monitoring

Monitor these metrics:
- Health endpoint uptime
- Database connection errors
- API quota usage (Apify, Exa, Gemini)
- Pipeline execution failures
- Model prediction errors

## Troubleshooting

### Backend fails to start
- Check Render logs for Python errors
- Verify `requirements.txt` dependencies
- Confirm PostgreSQL connection string

### Database connection errors
- Verify DATABASE_URL is Internal Database URL (not External)
- Check PostgreSQL instance is running
- Confirm STORAGE_BACKEND=postgres

### Frontend shows CORS errors
- Verify CORS_ORIGINS matches Vercel URL exactly
- Include protocol (https://)
- Redeploy backend after changing

### Model not loaded
- Check Render logs for file path errors
- Verify MODEL_PATH points to `model_artifacts/random_forest_model.joblib`
- Confirm scikit-learn==1.6.1 in requirements.txt

### Pipeline failures
- Check API credentials are valid (rotated)
- Verify Gemini quota not exhausted (20/day free tier)
- Review worker status in run endpoint
- Check Render logs for detailed errors

## Cost Estimates

**Free Tier (Development/Testing):**
- Render: $0
- Vercel: $0
- PostgreSQL: $0 (90-day retention)
- Total: $0/month

**Production (Paid Plans):**
- Render Backend: $7/month (Starter)
- PostgreSQL: $7/month (Basic, persistent)
- Vercel: $0 (free sufficient)
- Total: ~$14/month

**API Costs (Usage-based):**
- Apify: ~$0.50-2.00 per run (varies by actors)
- Exa: Pay-per-search (~$0.01-0.10 per query)
- Gemini: Free tier 20 requests/day, paid tier $0.00035/request
- Estimated: $10-30/month for daily runs

## Support

- GitHub Issues: https://github.com/Magenta91/HengSeng-Sentiment-Price-Prediction-agent/issues
- Render Docs: https://render.com/docs
- Vercel Docs: https://vercel.com/docs
