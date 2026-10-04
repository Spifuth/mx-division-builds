import { describe, it, expect, vi } from "vitest";

// dataImporter fetches 20 CSVs and reads localStorage at import time.
// statsService imports it, so without this mock the suite cannot even load.
vi.mock("./dataImporter", () => ({
  gearData: {},
  skillsData: {},
  weaponsData: {},
  specializationList: {},
  IsEverythingLoadedPromise: Promise.resolve(),
  VendorData: Promise.resolve({ Gear: {}, Weapons: [] }),
}));

import statsService from "./statsService";
import { readFileSync } from "node:fs";
import Papa from "papaparse";
import { STATS_ENUM } from "./utils";

describe("flatWeaponDamage", () => {
  it("applies additive damage percentages to base damage", () => {
    // 1000 base, +10% AWD, +5% weapon-type, +0% generic => 1150
    expect(statsService.flatWeaponDamage(1000, 10, 5, 0)).toBe(1150);
  });

  it("returns a number, not a string", () => {
    // .toFixed(0) returns a string. Every caller multiplies the result, so JS
    // coerces and the bug stays invisible -- until something uses + and gets
    // "1150100" instead of 1250.
    expect(typeof statsService.flatWeaponDamage(1000, 0, 0, 0)).toBe("number");
  });
});

describe("calcDmgToArmored", () => {
  it("adds the damage-to-armored percentage", () => {
    expect(statsService.calcDmgToArmored(1000, 15)).toBe(1150);
  });
  it("is a no-op at zero", () => {
    expect(statsService.calcDmgToArmored(1000, 0)).toBe(1000);
  });
});

describe("addCHDAndOrHSDOnTopOfFlatDamage", () => {
  it("ignores crit and headshot when both chances are zero", () => {
    expect(Number(statsService.addCHDAndOrHSDOnTopOfFlatDamage(1000, 50, 75, 0, 0))).toBe(1000);
  });
  it("applies crit damage scaled by crit chance", () => {
    // 1000 * (1 + (60 * 0.5)/100) = 1300
    expect(Number(statsService.addCHDAndOrHSDOnTopOfFlatDamage(1000, 60, 0, 0, 50))).toBe(1300);
  });
  it("applies headshot damage scaled by headshot chance", () => {
    // 1000 * (1 + (80 * 0.25)/100) = 1200
    expect(Number(statsService.addCHDAndOrHSDOnTopOfFlatDamage(1000, 0, 80, 25, 0))).toBe(1200);
  });
  // KNOWN DEFECT, deliberately left unfixed here. Task 4.2 fixed exactly this
  // .toFixed(0) string return in the sibling flatWeaponDamage; this one still
  // returns a string. It is latent, not active: every current consumer coerces
  // -- calcDmgToArmored/calcDmgToOutOfCover multiply it, and the UI's
  // roundValue() wraps it in Number(). Changing the return type is the owner's
  // call, not this task's.
  // `it.fails` asserts the body THROWS -- not specifically that this bug is
  // present -- so the suite stays green while documenting it. When someone
  // makes the function return a Number this line goes red: that is the signal
  // to drop `.fails`, not to weaken the test. The three tests above call the
  // same function unguarded, so a rename or deletion goes red there first;
  // if they ever go away, this one quietly stops meaning anything.
  it.fails("returns a string, not a number (should be a number)", () => {
    expect(typeof statsService.addCHDAndOrHSDOnTopOfFlatDamage(1000, 0, 0, 0, 0)).toBe("number");
  });
});

describe("calcReloadSpeed", () => {
  it("divides by the modifier -- faster reload means a smaller number", () => {
    expect(statsService.calcReloadSpeed(2000, 25)).toBe(1600);
  });
  it("is a no-op at zero", () => {
    expect(statsService.calcReloadSpeed(2000, 0)).toBe(2000);
  });
});

describe("getReloadSpeedModifier", () => {
  it("returns the base stat when there is no magazine", () => {
    expect(statsService.getReloadSpeedModifier(null, 10)).toBe(10);
  });
  // Spelled the way the shipped data spells it. These two used to say
  // "Reload Speed %", which no magazine row does, so they certified dead code.
  it("adds a positive magazine reload bonus", () => {
    expect(statsService.getReloadSpeedModifier({ pos: "Reload Speed", valPos: "15" }, 10)).toBe(25);
  });
  it("adds a negative magazine reload penalty", () => {
    expect(statsService.getReloadSpeedModifier({ neg: "Reload Speed", valNeg: "-20" }, 10)).toBe(-10);
  });
  it("recognises every magazine reload stat the shipped data carries", () => {
    // The guard the two tests above lacked: if the data ever renames the
    // stat again, this goes red instead of the bonus silently vanishing.
    const reloadMags = weaponMods.filter(
      (m) => m.Slot === "Magazine" && /reload/i.test(`${m.pos} ${m.neg}`),
    );
    expect(reloadMags.length, "the data must still have magazine reload rows").toBeGreaterThan(0);
    for (const m of reloadMags) {
      expect(statsService.getReloadSpeedModifier(m), m.Name).not.toBe(0);
    }
  });
});

describe("getAdditionalMagSizeFromTheMagazine", () => {
  it("returns zero without a magazine", () => {
    expect(statsService.getAdditionalMagSizeFromTheMagazine(null)).toBe(0);
  });
  it("returns the extra rounds value", () => {
    expect(statsService.getAdditionalMagSizeFromTheMagazine({ pos: "Extra Rounds", valPos: "21" })).toBe(21);
  });
  it("returns zero for a magazine that grants something else", () => {
    expect(statsService.getAdditionalMagSizeFromTheMagazine({ pos: "Reload Speed %", valPos: "15" })).toBe(0);
  });
});

describe("getStatValueFromGunMods", () => {
  it("sums positive and negative mod contributions across slots", () => {
    const weapon = {
      optic: { pos: "Critical Hit Chance", valPos: "5", neg: null, valNeg: 0 },
      muzzle: { pos: null, valPos: 0, neg: "Critical Hit Chance", valNeg: "-3" },
      magazine: null,
      "under barrel": null,
    };
    expect(statsService.getStatValueFromGunMods(weapon, "Critical Hit Chance")).toBe(2);
  });
});

// --- getWeaponStats against rows from the shipped data ---------------------
//
// The helper tests above call the helpers with hand-written mod objects, and two
// of them certified a branch the shipped data never reaches: they spelled the
// magazine stat "Reload Speed %", and every magazine row in
// public/data/weaponMods.csv says "Reload Speed". So the fixtures here are the
// real rows, parsed from the real file, and the assertions are on the numbers
// the weapon panel shows.
//
// These were written to pin what the code did BEFORE the reload fixes, so each
// fix lands as a deliberate change to an expectation below, not as a mystery
// shift in somebody's numbers.

const weaponMods = Papa.parse(
  readFileSync(new URL("../../public/data/weaponMods.csv", import.meta.url), "utf8"),
  { header: true, skipEmptyLines: true },
).data;

const mod = (name) => {
  const row = weaponMods.find((m) => m.Name === name);
  if (!row) throw new Error(`no weapon mod named ${name} in the shipped data`);
  return row;
};

// A neutral rifle: 30 rounds, a 2000 ms reload, and nothing on it that touches
// either. Only the field under test varies between cases.
const rifle = (over = {}) => ({
  name: "Test Rifle",
  "core 1": { stat: "Assault Rifle Damage", StatValue: 0 },
  "core 2": { stat: "Health Damage", StatValue: 0 },
  expertise: { StatValue: 0 },
  "attribute 1": { Stat: "Accuracy", StatValue: 0 },
  "base damage": "1000",
  hsd: "0",
  "mag size": "30",
  rpm: "600",
  "reload speed (ms)": "2000",
  optic: null,
  muzzle: null,
  magazine: null,
  "under barrel": null,
  ...over,
});

// Gear stats reach getWeaponStats through the module's `stats.Offensive`, the
// same bucket brand-set bonuses and SHD levels land in.
const withGear = (offensive = {}) => {
  statsService.resetStats();
  statsService.addStatsFromSHD(
    Object.entries(offensive).map(([name, value]) => ({ name, type: "O", value })),
  );
};

describe("getWeaponStats — reload speed, against shipped data", () => {
  it("leaves a bare rifle's reload alone", () => {
    withGear();
    expect(statsService.getWeaponStats(rifle(), "Primary").reloadSpeed).toBe(2000);
  });

  it("applies a magazine's reload bonus", () => {
    // Short Spring .45 ACP Mag: +20 Reload Speed.
    withGear();
    const magazine = mod("Short Spring .45 ACP Mag");
    expect(magazine.pos).toBe("Reload Speed");
    // Was 2000: the bonus never applied.
    expect(statsService.getWeaponStats(rifle({ magazine }), "Primary").reloadSpeed)
      .toBeCloseTo(2000 / 1.2, 6);
  });

  it("applies a magazine's reload penalty", () => {
    // Oversized .45 ACP Mag: +20 Extra Rounds, -10 Reload Speed.
    withGear();
    const magazine = mod("Oversized .45 ACP Mag");
    expect(magazine.neg).toBe("Reload Speed");
    const s = statsService.getWeaponStats(rifle({ magazine }), "Primary");
    expect(s.totalMagSize).toBe(50);
    // Was 2000: the penalty never applied either.
    expect(s.reloadSpeed).toBeCloseTo(2000 / 0.9, 6);
  });

  it("counts gear reload speed once", () => {
    // Fenris Group AB, Ongoing Directive and Umbra Initiative each give
    // +30 Reload Speed % (public/data/brandsetBonuses.csv).
    withGear({ [STATS_ENUM.RELOAD_SPEED_PERC]: 30 });
    // Was 2000 / 1.6 = 1250: the 30 was counted twice.
    expect(statsService.getWeaponStats(rifle(), "Primary").reloadSpeed)
      .toBeCloseTo(2000 / 1.3, 6);
  });

  it("adds gear and magazine reload together, each once", () => {
    // +30 from gear and +20 from Short Spring: 2000 / 1.5.
    withGear({ [STATS_ENUM.RELOAD_SPEED_PERC]: 30 });
    const magazine = mod("Short Spring .45 ACP Mag");
    expect(statsService.getWeaponStats(rifle({ magazine }), "Primary").reloadSpeed)
      .toBeCloseTo(2000 / 1.5, 6);
  });

  it("counts a weapon attribute's reload speed once", () => {
    // The control for the test above: the same stat from the weapon's own
    // attribute goes through getStatValueFromGunAndGear only, so it is single.
    // 12 is the shipped weaponAttributes.csv value.
    withGear();
    const attr = { Stat: STATS_ENUM.RELOAD_SPEED_PERC, StatValue: 12 };
    expect(statsService.getWeaponStats(rifle({ "attribute 1": attr }), "Primary").reloadSpeed)
      .toBeCloseTo(2000 / 1.12, 6);
  });
});

describe("getWeaponStats — magazine size, against shipped data", () => {
  it("adds a magazine's extra rounds", () => {
    withGear();
    const magazine = mod("Extended 9mm Mag"); // +20 Extra Rounds
    expect(statsService.getWeaponStats(rifle({ magazine }), "Primary").totalMagSize)
      .toBe(50);
  });

  it("applies gear Magazine Size % as a percentage of the whole magazine", () => {
    // True Patriot / Tipping Scales: +30 Magazine Size %.
    withGear({ [STATS_ENUM.MAGAZINE_SIZE_PERC]: 30 });
    const magazine = mod("Extended 9mm Mag");
    expect(statsService.getWeaponStats(rifle({ magazine }), "Primary").totalMagSize)
      .toBeCloseTo(50 * 1.3, 6);
  });

  it("has no gear flat-mag-size stat for the extra argument to carry", () => {
    // statsService passes `stats.Offensive[STATS_ENUM.MAG_SIZE]` as a second
    // argument to the one-argument getAdditionalMagSizeFromTheMagazine. That
    // looks like a gear bonus being dropped. It is not: STATS_ENUM has no
    // MAG_SIZE (that key lives on WEAPON_PROP_ENUM), so the argument is always
    // stats.Offensive["undefined"] -- and no gear table names a flat mag stat;
    // gear magazine bonuses are all "Magazine Size %", applied above.
    expect(STATS_ENUM.MAG_SIZE).toBeUndefined();
  });
});
