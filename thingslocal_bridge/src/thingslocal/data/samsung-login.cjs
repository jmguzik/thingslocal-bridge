// Use the unmodified official SmartThings CLI Samsung login implementation.
const fs = require('node:fs');
const path = require('node:path');
process.umask(0o077);
const workspace = process.env.THINGSLOCAL_WORKSPACE;
const prefix = process.env.SMARTTHINGS_CLI_PREFIX;
if (!workspace || !prefix) throw new Error('Workspace and official CLI prefix are required');
const modules = path.join(prefix, 'node_modules');
// Keep the official SDK's OAuth/PKCE flow, bind its HTTP callback to loopback,
// and expose its local entry URL when desktop auto-opening does not work.
const http = require('node:http');
const originalListen = http.Server.prototype.listen;
http.Server.prototype.listen = function (...args) {
  if (typeof args[0] === 'number' && (typeof args[1] === 'function' || args.length === 1)) {
    args.splice(1, 0, '127.0.0.1');
  }
  this.once('listening', () => {
    const address = this.address();
    if (address && typeof address === 'object') {
      console.log(`If no browser appeared, open http://localhost:${address.port}/start on this computer.`);
    }
  });
  return originalListen.apply(this, args);
};
const log4js = require(path.join(modules, 'log4js'));
log4js.configure({ appenders: { console: { type: 'console' } }, categories: { default: { appenders: ['console'], level: 'off' } } });
const { LoginAuthenticator, defaultClientIdProvider } = require(path.join(modules, '@smartthings/cli-lib/dist/login-authenticator.js'));
const credentials = path.join(workspace, 'samsung-cli-credentials.json');
fs.mkdirSync(workspace, { recursive: true, mode: 0o700 });
fs.chmodSync(workspace, 0o700);
LoginAuthenticator.init(credentials);
const auth = new LoginAuthenticator('thingslocal', defaultClientIdProvider, 'thingslocal-bridge/0.1.0');
console.log('Opening the official Samsung sign-in flow. Tokens remain in the private workspace.');
auth.login().then(() => { fs.chmodSync(credentials, 0o600); console.log('Samsung sign-in completed.'); })
.catch(() => { console.error('Samsung sign-in failed.'); process.exitCode = 1; })
.finally(() => log4js.shutdown());
