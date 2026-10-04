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

export interface Device {
  slug: string;
  label: string;
  online: boolean;
  video: Capability;
  audio: Capability;
}

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
  if (d.video.allowed && d.audio.allowed) return 'av';
  if (d.video.allowed) return 'video';
  if (d.audio.allowed) return 'audio';
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
