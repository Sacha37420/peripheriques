import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable } from 'rxjs';
import {
  AdminDevice, AdminDeviceList, AdminDevicePatch, Device, Grant, GrantCreate, GrantDevice, Materiel,
  GrantPatch, Me, Segment, Ticket, TicketRequest,
} from './models';

interface EnvWindow {
  __env?: { apiUrl?: string };
}

/** Encode chaque segment d'un chemin MediaMTX sans toucher aux « / » qui les séparent. */
function encodePath(path: string): string {
  return path.split('/').map(encodeURIComponent).join('/');
}

/** Client de l'API I3 (CONTRAT v1) + construction des URLs média I6. */
@Injectable({ providedIn: 'root' })
export class ApiService {
  private http = inject(HttpClient);

  /** Ex. https://<domaine>/peripheriques-api (pas de / final). */
  get base(): string {
    return ((window as unknown as EnvWindow).__env?.apiUrl ?? 'http://localhost:8101').replace(/\/+$/, '');
  }

  /** Origine de l'API — les URLs absolues-chemin rendues par l'API (ticket) s'y résolvent. */
  private get origin(): string {
    return new URL(this.base, window.location.href).origin;
  }

  // ── I3 ────────────────────────────────────────────────────────────────
  getMe(): Observable<Me> {
    return this.http.get<Me>(`${this.base}/api/me/`);
  }

  getDevices(): Observable<Device[]> {
    return this.http.get<Device[]>(`${this.base}/api/devices/`);
  }

  /** v4 — admin : TOUS les périphériques (sélecteur de la page Droits), indépendamment de tout Grant. */
  getGrantDevices(): Observable<GrantDevice[]> {
    return this.http.get<GrantDevice[]>(`${this.base}/api/grants/devices/`);
  }

  getGrants(): Observable<Grant[]> {
    return this.http.get<Grant[]>(`${this.base}/api/grants/`);
  }

  createGrant(body: GrantCreate): Observable<Grant> {
    return this.http.post<Grant>(`${this.base}/api/grants/`, body);
  }

  updateGrant(id: number, body: GrantPatch): Observable<Grant> {
    return this.http.patch<Grant>(`${this.base}/api/grants/${id}/`, body);
  }

  deleteGrant(id: number): Observable<void> {
    return this.http.delete<void>(`${this.base}/api/grants/${id}/`);
  }

  /** v6 — admin : catalogue matériel (I7), servi tel quel. 503 si indisponible. */
  getMateriel(): Observable<Materiel> {
    return this.http.get<Materiel>(`${this.base}/api/admin/materiel/`);
  }

  /** Admins des droits : tous les périphériques déjà vus (inventaire de lab-media). */
  getAdminDevices(): Observable<AdminDeviceList> {
    return this.http.get<AdminDeviceList>(`${this.base}/api/admin/devices/`);
  }

  /** Renommer (label) et/ou interrupteur d'enregistrement (record, relayé à lab-media). */
  updateAdminDevice(id: string, body: AdminDevicePatch): Observable<AdminDevice> {
    return this.http.patch<AdminDevice>(`${this.base}/api/admin/devices/${encodeURIComponent(id)}/`, body);
  }

  /** Oublier un périphérique débranché : ses droits sont supprimés avec lui. */
  forgetAdminDevice(id: string): Observable<void> {
    return this.http.delete<void>(`${this.base}/api/admin/devices/${encodeURIComponent(id)}/`);
  }

  /** date = YYYY-MM-DD, journée Europe/Paris. */
  getRecordings(device: string, date: string): Observable<Segment[]> {
    const params = new HttpParams().set('device', device).set('date', date);
    return this.http.get<Segment[]>(`${this.base}/api/recordings/`, { params });
  }

  createTicket(body: TicketRequest): Observable<Ticket> {
    return this.http.post<Ticket>(`${this.base}/api/recordings/ticket/`, body);
  }

  /** URL de lecture (<video src>) à partir de ticket.url (« /peripheriques-api/… »). */
  ticketUrl(ticket: Ticket): string {
    return new URL(ticket.url, this.origin).toString();
  }

  // ── URLs navigateur de la brique lab-media (même origine que l'API, via Caddy) ──
  // lab-media/API.md §3 : /lab-media-webrtc/<chemin>/whep et /lab-media-hls/<chemin>/index.m3u8.
  // <chemin> est celui que donne l'inventaire (I1), tel quel : seuls ses segments sont encodés.
  whepUrl(path: string): string {
    return `${this.origin}/lab-media-webrtc/${encodePath(path)}/whep`;
  }

  hlsUrl(path: string): string {
    return `${this.origin}/lab-media-hls/${encodePath(path)}/index.m3u8`;
  }
}
