// vue.config.js
module.exports = {
	runtimeCompiler: true,
	publicPath: process.env.BASE_URL,

	configureWebpack: {
		output: {
			// webpack 4 hashes modules with md4, which OpenSSL 3 (Node 17+) removed —
			// it throws ERR_OSSL_EVP_UNSUPPORTED on the first module it builds.
			// Naming a still-supported algorithm fixes it at the source, which beats
			// re-enabling the whole legacy provider via NODE_OPTIONS.
			hashFunction: "sha256",
		},
	},

	// Dev-server binding is driven entirely by env (set in the gitignored .env.local)
	// so no internal/tailnet address is ever committed to this public fork.
	// Defaults are the stock vue-cli behaviour when the vars are absent.
	devServer: {
		host: process.env.DEV_HOST || "localhost",
		port: Number(process.env.DEV_PORT) || 8080,
		disableHostCheck: true,
	},
};
// TODO https://medium.com/hceverything/how-to-show-your-app-version-from-package-json-in-your-vue-application-11e882b97d8c
