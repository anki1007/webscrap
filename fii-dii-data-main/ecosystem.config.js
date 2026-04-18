// PM2 Ecosystem Config — Hostinger Node.js Hosting
// Usage: pm2 start ecosystem.config.js
module.exports = {
  apps: [{
    name: 'fii-dii-dashboard',
    script: 'server.js',
    instances: 1,
    autorestart: true,
    watch: false,
    max_memory_restart: '256M',
    env: {
      NODE_ENV: 'production',
      PORT: 3000
    }
  }]
};
