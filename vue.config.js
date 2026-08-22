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
		// webpack-dev-server 3 rejects requests whose Host header isn't the bind
		// address; needed when reaching this over a tailnet IP or MagicDNS name.
		disableHostCheck: true,

		// The upstream data provider only sends Access-Control-Allow-Origin for
		// https://mxswat.github.io -- every other origin gets a 200 with no CORS
		// header, which the browser then blocks. curl can't see this (it ignores
		// CORS), and dataImporter surfaces the rejection as App.vue's misleading
		// "too many people are connected to the server" screen.
		//
		// Proxying makes the requests same-origin from the browser's point of
		// view, so CORS never applies. Server-to-server the header is irrelevant.
		// Point VUE_APP_DATA_URL_* at /td2data/<endpoint> to use this.
		proxy: {
			"/td2data": {
				target: "https://buildstation.app",
				changeOrigin: true,
				pathRewrite: { "^/td2data": "/api/td2/v2/data/mx" },
			},
		},
	},
};
// TODO https://medium.com/hceverything/how-to-show-your-app-version-from-package-json-in-your-vue-application-11e882b97d8c
