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
  it("adds a positive magazine reload bonus", () => {
    expect(statsService.getReloadSpeedModifier({ pos: "Reload Speed %", valPos: "15" }, 10)).toBe(25);
  });
  it("adds a negative magazine reload penalty", () => {
    expect(statsService.getReloadSpeedModifier({ neg: "Reload Speed %", valNeg: "-20" }, 10)).toBe(-10);
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
