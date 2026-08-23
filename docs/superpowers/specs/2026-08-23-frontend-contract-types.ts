// Shared types for the TD2 Build Page reference data + build contract.
// Mirrors the API contract (mock-data backed for now; a separate team owns the real dataset service).

export type WeaponType =
  | 'Assault Rifle'
  | 'SMG'
  | 'LMG'
  | 'Rifle'
  | 'Marksman Rifle'
  | 'Shotgun'
  | 'Pistol'

export type WeaponQuality = 'Standard' | 'Named' | 'Exotic' | 'Signature'

export interface Weapon {
  name: string
  type: WeaponType
  quality: WeaponQuality
  damage: number
  rpm: number
  magazine: number
  optimalRange: number
  accuracy: number
  stability: number
  handling: number
  talents: string[]
  brand?: string
  image?: string
}

export type GearSlot = 'mask' | 'chest' | 'backpack' | 'gloves' | 'holster' | 'kneepads'

export type GearQuality = 'Standard' | 'Gear Set' | 'Named'

export interface GearPiece {
  id: string
  name: string
  slot: GearSlot
  quality: GearQuality
  brand?: string
  gearSet?: string
  armor: number
  coreAttribute: string
  attributeSlots: number
  talent?: string
  mod: boolean
  image?: string
}

export interface BrandSetBonus {
  count: number
  bonus: string
}

export interface Brand {
  id: string
  name: string
  pieces: number
  bonuses: BrandSetBonus[]
}

// Dedicated named Gear Sets (distinct from civilian Brands) — full outfits
// with their own 1-4pc bonus ladder, referenced by GearPiece.gearSet.
export interface GearSet {
  id: string
  name: string
  pieces: number
  bonuses: BrandSetBonus[]
}

export interface SkillVariant {
  id: string
  name: string
  description: string
}

export interface SkillTierStat {
  tier: number
  label: string
  value: string
}

export interface Skill {
  id: string
  name: string
  description: string
  variants: SkillVariant[]
  tierStats: SkillTierStat[]
}

export interface Specialization {
  id: string
  name: string
  signatureWeapon: string
  description: string
  perks: string[]
}

export type TalentCategory = 'gear' | 'weapon'

export interface Talent {
  id: string
  name: string
  category: TalentCategory
  description: string
}

export type AttributeCategory = 'gear' | 'weapon'

export interface AttributeCap {
  id: string
  name: string
  category: AttributeCategory
  slot: string
  quality: string
  min: number
  max: number
  unit: '%' | 'flat'
}

export type ModCategory = 'gear' | 'weapon' | 'skill'

export interface Mod {
  id: string
  name: string
  category: ModCategory
  slot: string
  effect: string
}

export const BUILD_SLOT_KEYS = [
  'Mask',
  'Backpack',
  'Chest',
  'Gloves',
  'Holster',
  'Kneepads',
  'Primary',
  'Secondary',
  'SideArm',
  'Specialization',
  'Skill1',
  'Skill2',
] as const

export type BuildSlotKey = (typeof BUILD_SLOT_KEYS)[number]

export interface BuildLoadout {
  Mask: string | null
  Backpack: string | null
  Chest: string | null
  Gloves: string | null
  Holster: string | null
  Kneepads: string | null
  Primary: string | null
  Secondary: string | null
  SideArm: string | null
  Specialization: string | null
  Skill1: string | null
  Skill2: string | null
}

// SHD Tech — four perk nodes, each with four stats levelled independently
// from 0-50 (matches the in-game "Stats Provided (Max Level 50)" table).
export type ShdNodeKey = 'offense' | 'defense' | 'handling' | 'utility'

export interface ShdStatDef {
  key: string
  label: string
}

export interface ShdNodeDef {
  key: ShdNodeKey
  label: string
  stats: ShdStatDef[]
}

export const SHD_STAT_MAX = 50

export const SHD_NODES: ShdNodeDef[] = [
  {
    key: 'offense',
    label: 'Offense',
    stats: [
      { key: 'weaponDamage', label: 'Weapon Damage' },
      { key: 'headshotDamage', label: 'Headshot Damage' },
      { key: 'criticalHitChance', label: 'Critical Hit Chance' },
      { key: 'criticalHitDamage', label: 'Critical Hit Damage' },
    ],
  },
  {
    key: 'defense',
    label: 'Defense',
    stats: [
      { key: 'totalHealth', label: 'Total Health' },
      { key: 'totalArmor', label: 'Total Armor' },
      { key: 'hazardProtection', label: 'Hazard Protection' },
      { key: 'explosiveResistance', label: 'Explosive Resistance' },
    ],
  },
  {
    key: 'handling',
    label: 'Handling',
    stats: [
      { key: 'accuracy', label: 'Accuracy' },
      { key: 'stability', label: 'Stability' },
      { key: 'ammoCapacity', label: 'Ammo Capacity' },
      { key: 'reloadSpeed', label: 'Reload Speed' },
    ],
  },
  {
    key: 'utility',
    label: 'Utility',
    stats: [
      { key: 'skillRepair', label: 'Skill Repair' },
      { key: 'skillDamage', label: 'Skill Damage' },
      { key: 'skillDuration', label: 'Skill Duration' },
      { key: 'skillHaste', label: 'Skill Haste' },
    ],
  },
]

// Node key -> stat key -> level (0-50).
export type ShdNodeLevels = Record<string, number>

export type ShdPerks = Record<ShdNodeKey, ShdNodeLevels>

export function createDefaultShdPerks(): ShdPerks {
  const perks = {} as ShdPerks
  for (const node of SHD_NODES) {
    const levels: ShdNodeLevels = {}
    for (const stat of node.stats) {
      levels[stat.key] = 0
    }
    perks[node.key] = levels
  }
  return perks
}

// Clamps/normalizes an arbitrary payload into a well-formed ShdPerks object
// so the API never trusts client-supplied levels or node/stat keys directly.
export function sanitizeShdPerks(input: unknown): ShdPerks {
  const source = (input && typeof input === 'object' ? input : {}) as Record<string, unknown>
  const perks = {} as ShdPerks
  for (const node of SHD_NODES) {
    const nodeSource = (source[node.key] && typeof source[node.key] === 'object'
      ? source[node.key]
      : {}) as Record<string, unknown>
    const levels: ShdNodeLevels = {}
    for (const stat of node.stats) {
      const raw = Number(nodeSource[stat.key])
      levels[stat.key] = Number.isFinite(raw) ? Math.max(0, Math.min(SHD_STAT_MAX, Math.round(raw))) : 0
    }
    perks[node.key] = levels
  }
  return perks
}

export function shdNodeTotal(perks: ShdPerks, node: ShdNodeKey): number {
  return Object.values(perks[node]).reduce((sum, level) => sum + level, 0)
}

export interface Build {
  id: string
  name: string
  notes: string
  shdLevel: number
  shdPerks: ShdPerks
  loadout: BuildLoadout
  views: number
  createdAt: string
  updatedAt: string
}

export interface BuildComputeResult {
  weapon_damage: number
  dps: number
  ttk: number
  armor: number
  health: number
  skill_stats: Record<string, string>
  shd_bonus?: Record<ShdNodeKey, number>
}

export interface Meta {
  datasetVersion: string
  tables: { name: string; rows: number }[]
}
