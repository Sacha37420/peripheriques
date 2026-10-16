/**
 * Types de l'API I3 (chantiers/peripheriques/CONTRAT.md, v1) — recopiés tels quels.
 * Tous les horodatages sont des chaînes ISO 8601 UTC avec « Z ».
 */

export interface Me {
  email: string;
  is_admin: boolean;
}

export interface Capability {
  allowed: boolean;
  /** Début de la fenêtre de relecture ; null pour un admin = tout l'historique conservé. */
  since: string | null;
}

/**
 * Chemins de lecture d'un périphérique, tels que lab-media les donne (contrat webcams, I1).
 * Toujours utilisés tels quels, jamais recomposés. `audio` est null sans micro.
 */
export interface StreamPaths {
  av: string | null;
  video: string | null;
  audio: string | null;
}

export interface Device {
  /** Identifiant I0 (ex. « usb-32e4-9230-p3-4 », « webcam-ancienne »). */
  slug: string;
  /** Nom d'affichage — texte brut, toujours rendu par interpolation. */
  label: string;
  online: boolean;
  has_audio: boolean;
  paths: StreamPaths;
  video: Capability;
  audio: Capability;
}

/** État d'inventaire (I1) ; '' = état inconnu. */
export type DeviceState = 'live' | 'waiting' | 'starting' | 'error' | 'offline' | 'legacy' | '';

/** GET /api/grants/devices/ (admin) : tous les périphériques, pour la gestion des droits uniquement. */
export interface GrantDevice {
  slug: string;
  label: string;
  online: boolean;
  has_audio: boolean;
  state: DeviceState;
  connected: boolean;
}

/** GET /api/admin/devices/ (admins des droits) : un périphérique déjà vu, branché ou non. */
export interface AdminDevice {
  id: string;
  label: string;
  model: string;
  vendor_id: string;
  product_id: string;
  serial: string;
  usb_port: string;
  has_audio: boolean;
  connected: boolean;
  state: DeviceState;
  error_detail: string;
  record: boolean;
  first_seen: string | null;
  last_seen: string | null;
  paths: StreamPaths;
  /** Faux : absent de la dernière liste lue chez lab-media. */
  listed: boolean;
  grants: number;
}

export interface AdminDeviceList {
  inventory: { reachable: boolean | null; checked_at: string | null };
  items: AdminDevice[];
}

export interface AdminDevicePatch {
  label?: string;
  record?: boolean;
}

/** Libellés français des états d'inventaire. */
export const STATE_LABEL: Record<DeviceState, string> = {
  live: 'En direct',
  waiting: 'En attente (plafond de capture atteint)',
  starting: 'Démarrage',
  error: 'En erreur',
  offline: 'Débranché',
  legacy: 'Ancienne webcam',
  '': 'État inconnu',
};

export interface Grant {
  id: number;
  email: string;
  device: string;
  video: boolean;
  audio: boolean;
  video_since: string | null;
  audio_since: string | null;
  granted_by: string;
  updated_at: string;
}

export interface GrantCreate {
  email: string;
  device: string;
  video: boolean;
  audio: boolean;
}

export interface GrantPatch {
  video?: boolean;
  audio?: boolean;
}

export type Track = 'video' | 'audio';

export interface Segment {
  start: string;
  end: string;
  duration_s: number;
  tracks: Track[];
}

export interface TicketRequest {
  device: string;
  start: string;
  duration_s: number;
}

export interface Ticket {
  url: string;
  expires_in: number;
  tracks: Track[];
}

/** Chemin MediaMTX lu en direct (I1/I6). */
export type StreamKind = 'av' | 'video' | 'audio';

/** av si vidéo+audio, video ou audio sinon, null si aucun droit (I6). */
export function streamKind(d: Device): StreamKind | null {
  if (d.video.allowed && d.audio.allowed && d.paths.audio) return 'av';
  if (d.video.allowed) return 'video';
  if (d.audio.allowed && d.paths.audio) return 'audio';
  return null;
}

/**
 * Transforme une erreur HTTP de l'API en messages affichables TELS QUELS
 * (corps DRF : {"champ": ["msg", …]} ou {"detail": "msg"}).
 */
export function apiErrorMessages(err: unknown): string[] {
  const e = err as { status?: number; error?: unknown; message?: string };
  const body = e?.error;
  const out: string[] = [];
  if (body && typeof body === 'object') {
    for (const v of Object.values(body as Record<string, unknown>)) {
      if (Array.isArray(v)) out.push(...v.map(String));
      else if (v != null) out.push(String(v));
    }
  } else if (typeof body === 'string' && body.trim() && !body.trimStart().startsWith('<')) {
    out.push(body.trim());
  }
  if (!out.length) {
    out.push(e?.status ? `Erreur ${e.status}` : 'Erreur réseau — serveur injoignable.');
  }
  return out;
}

// ── I7 — Catalogue matériel (v6, admin) ────────────────────────────────
export type MaterielFamily = 'A' | 'B' | 'C';
export type Continuous247 = 'oui' | 'non' | 'seulement sur secteur' | 'non confirmé';
export type MaterielVerdict = 'recommandé' | 'possible' | 'déconseillé pour ce service';

export interface MaterielItem {
  name: string;
  brand: string;
  family: MaterielFamily;
  connection: string;
  power: string;
  battery: boolean;
  night_vision: string | null;
  tracking: string | null;
  audio: string | null;
  rtsp_or_uvc: string;
  continuous_24_7: Continuous247;
  codec: string;
  price_eur: string | null;
  links: { label: string; url: string }[];
  sources: string[];
  caveats: string[];
  verdict: MaterielVerdict;
}

export interface Materiel {
  updated: string;
  summary: string[];
  items: MaterielItem[];
}
