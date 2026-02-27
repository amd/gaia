# Railway Deployment Guide

Deploy the GAIA website to Railway with automated deployments from GitHub.

## Prerequisites

- Railway account (free tier available)
- GitHub repo: `github.com/amd/gaia-website` (private)
- Website code committed to repo

## Step 1: Initial Setup

1. **Go to Railway**: https://railway.app
2. **Sign in with GitHub**
3. **New Project** → **Deploy from GitHub repo**
4. **Select** `amd/gaia-website`

## Step 2: Configure Build Settings

Railway should auto-detect Astro, but verify these settings:

**Build Command:**
```bash
npm install && npm run build
```

**Start Command:**
```bash
npm run preview
```

**Output Directory:**
```
dist
```

## Step 3: Environment Variables (Optional)

No environment variables needed for basic deployment.

If you add analytics later:
- `PUBLIC_ANALYTICS_ID` - Your analytics tracking ID

## Step 4: Deploy

1. **Click "Deploy"**
2. Railway will:
   - Install dependencies
   - Build the site
   - Serve from `dist/`
3. **Wait 2-3 minutes** for first deploy

## Step 5: Custom Domain

1. Go to **Settings → Domains**
2. Add custom domain: `amd-gaia.ai`
3. Add DNS records (Railway provides instructions):
   ```
   Type: CNAME
   Name: @
   Value: [your-app].railway.app
   ```

## Automated Deployments

✅ **Already configured!** Railway automatically deploys when you push to GitHub.

**Workflow:**
```
1. Push to github.com/amd/gaia-website
2. Railway detects commit
3. Builds and deploys automatically
4. Live in ~2 minutes
```

## Branch Deployments

**Main branch** → Production (amd-gaia.ai)
**Other branches** → Preview URLs

To enable:
1. Settings → **Environments**
2. Create `production` environment for `main` branch
3. All other branches get preview URLs

## Monitoring

**View logs:**
- Railway Dashboard → Your project → **Deployments**
- Click any deployment to see build logs
- Real-time streaming logs

**Metrics:**
- CPU usage
- Memory usage
- Request volume

## Rollback

If a deployment fails:
1. Go to **Deployments**
2. Find last working deployment
3. Click **Redeploy**

## Cost

**Free Tier:**
- $5 free credit per month
- 500 hours of execution
- Unlimited projects

**Starter Plan ($5/mo):**
- More execution hours
- Custom domains
- Priority support

Static sites like this use minimal resources - free tier is usually enough.

## Alternative: Cloudflare Pages

If you prefer Cloudflare Pages (recommended in README):

1. Go to Cloudflare dashboard
2. Pages → **Create project**
3. Connect `amd/gaia-website`
4. Build command: `npm run build`
5. Output: `dist`
6. Deploy

**Benefits:**
- Faster global CDN
- Unlimited bandwidth
- Better DDoS protection
- Free SSL

## Troubleshooting

**Build fails:**
- Check Railway logs for errors
- Verify `package.json` scripts are correct
- Test locally with `npm run build`

**Site not loading:**
- Check if build succeeded
- Verify start command is correct
- Check Railway service logs

**Slow builds:**
- Railway caches `node_modules`
- First build is slower (~3 min)
- Subsequent builds are faster (~1 min)

## Next Steps

After successful deployment:
1. ✅ Test the live site
2. ✅ Set up custom domain
3. ✅ Enable branch previews
4. ✅ Add analytics (optional)
5. ✅ Monitor performance

---

**Pro Tip:** Use Railway CLI for faster deployments:
```bash
npm install -g @railway/cli
railway login
railway link
railway up  # Deploy from terminal
```
