import Papa from "papaparse";
import {
	csvToArrayWithKeys,
	getAppRootPath,
	groupArrayOfObjectsByKey,
} from "./utils";

// Data ships with the app as static CSV under public/data/. It used to come
// from a third-party API behind twenty VUE_APP_DATA_URL_* env vars; that
// indirection is why a fresh clone rendered a blank app, since upstream never
// committed the .env those vars lived in. A path built from the table name
// needs no configuration and cannot be misconfigured.
const dataUrl = (name) => `${getAppRootPath()}data/${name}.csv`;
let IsEverythingLoadedPromiseResolve, IsEverythingLoadedPromiseReject;

const IsEverythingLoadedPromise = new Promise(function(resolve, reject) {
	IsEverythingLoadedPromiseResolve = resolve;
	IsEverythingLoadedPromiseReject = reject;
});

const ClientDBVersion = localStorage.getItem("localDBversion") || "";
let RemoteDBVersion = process.env.VUE_APP_DB_VERSION;

function getFromGoogleDrive(dataSources, listToPopulate) {
	for (let i = 0; i < dataSources.length; i++) {
		const DataTableName = dataSources[i].key;
		const url = dataSources[i].url;
		if (!ClientDBVersion || ClientDBVersion !== RemoteDBVersion) {
			listToPopulate[DataTableName] = new Promise((resolve, reject) => {
				Papa.parse(url, {
					download: true,
					complete: function(incomingData, fileName) {
						try {
							// console.log("Parsing complete:", incomingData, fileName);
							const headers = incomingData.data.shift();
							let result = csvToArrayWithKeys(headers, incomingData.data);
							localStorage.setItem(DataTableName, JSON.stringify(result));
							resolve(result);
						} catch (error) {
							reject(
								"Something when wrong during the download of one data table"
							);
						}
					},
					error: function returnError(params) {
						reject(
							"Something when wrong during the download of one data table"
						);
					},
				});
			});
		} else {
			const localData = localStorage.getItem(DataTableName);
			listToPopulate[DataTableName] = Promise.resolve(JSON.parse(localData));
		}
	}
}

const urls = ["gear", "weapons"];

const VendorPromises = Promise.all(
	urls.map((url) =>
		fetch(`${getAppRootPath()}vendors/${url}.json?${new Date().toISOString()}`)
			.then((e) => e.json())
			.catch((error) => {
				console.warn(`Failed to parse ${url} vendor data:`, error);
				return []; // Return empty array if json() fails
			})
	)
).then((data) => {
	const gear = data[0].map((g) => {
		return {
			Name: g.rarity?.includes("named") ? g.name : g.brand,
			Slot: g.slot,
			Vendor: g.vendor,
		};
	});

	const weapons = data[1].map((g) => {
		return {
			Name: g.rarity?.includes("named")
				? g.name?.replace(/-.*/i, "").trim()
				: g.name,
			Vendor: g.vendor,
		};
	});
	const gearBySlot = groupArrayOfObjectsByKey(gear, "Slot");
	return {
		Gear: gearBySlot,
		Weapons: weapons,
	};
});

// DB.version path
const path = getAppRootPath() + "DB.Version";

// Disable browser cache for the DB version using new Date
fetch(`${path}?${new Date().toISOString()}`, { method: "GET" })
	.then((response) => response.blob())
	.then((blob) => blob.text())
	.then((DownloadedDBVersion) => {
		RemoteDBVersion = DownloadedDBVersion;
		if (DownloadedDBVersion !== ClientDBVersion) {
			window.localStorage.clear(); //clear all localstorage after new per sheet versioning
		}
		getFromGoogleDrive(wearableSource, gearData);
		getFromGoogleDrive(weaponsDataSource, weaponsData);
		getFromGoogleDrive(skillsDataSource, skillsData);
		getFromGoogleDrive(specializationListSource, specializationList);

		Promise.all([
			...Object.values(gearData),
			...Object.values(skillsData),
			...Object.values(weaponsData),
			...Object.values(specializationList),
			VendorPromises,
		])
			.then(() => {
				localStorage.setItem("localDBversion", RemoteDBVersion);
				IsEverythingLoadedPromiseResolve();
			})
			.catch(() => {
				IsEverythingLoadedPromiseReject();
				window.localStorage.clear();
				// location.reload();
			});
	});

const skillsData = {
	Skills: null,
	SkillStats: null,
	SkillMods: null,
};

const skillsDataSource = [
	{
		key: "Skills",
		url: dataUrl("skill"),
	},
	{
		key: "SkillStats",
		url: dataUrl("skillStats"),
	},
	{
		key: "SkillMods",
		url: dataUrl("skillMods"),
	},
];

const weaponsData = {
	Weapons: null,
	WeaponAttributes: null,
	WeaponMods: null,
	WeaponTalents: null,
};

const weaponsDataSource = [
	{
		key: "Weapons",
		url: dataUrl("weapon"),
	},
	{
		key: "WeaponAttributes",
		url: dataUrl("weaponAttributes"),
	},
	{
		key: "WeaponMods",
		url: dataUrl("weaponMods"),
	},
	{
		key: "WeaponTalents",
		url: dataUrl("weaponTalents"),
	},
];

const specializationList = {
	Specialization: null,
};

const specializationListSource = [
	{
		key: "Specialization",
		url: dataUrl("specialization"),
	},
];

const gearData = {
	Chest: null,
	Gloves: null,
	Holster: null,
	Kneepads: null,
	Backpack: null,
	Mask: null,
	Attributes: null,
	GearMods: null,
	GearTalents: null,
	BrandSetBonuses: null,
	StatsMapping: null,
	BrandsData: null,
};

const wearableSource = [
	{
		key: "Chest",
		url: dataUrl("chest"),
	},
	{
		key: "Gloves",
		url: dataUrl("gloves"),
	},
	{
		key: "Holster",
		url: dataUrl("holster"),
	},
	{
		key: "Kneepads",
		url: dataUrl("kneepads"),
	},
	{
		key: "Backpack",
		url: dataUrl("backpack"),
	},
	{
		key: "Mask",
		url: dataUrl("mask"),
	},
	{
		key: "Attributes",
		url: dataUrl("gearAttributes"),
	},
	{
		key: "GearMods",
		url: dataUrl("gearMods"),
	},
	{
		key: "GearTalents",
		url: dataUrl("gearTalents"),
	},
	{
		key: "BrandSetBonuses",
		url: dataUrl("brandsetBonuses"),
	},
	{
		key: "StatsMapping",
		url: dataUrl("statsMapping"),
	},
	{
		key: "BrandsData",
		url: dataUrl("brands"),
	},
];

export {
	IsEverythingLoadedPromise,
	gearData,
	skillsData,
	weaponsData,
	specializationList,
	VendorPromises as VendorData,
};
