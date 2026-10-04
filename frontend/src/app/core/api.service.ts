import { Injectable, inject } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable } from 'rxjs';
import {
  Device, Grant, GrantCreate, GrantPatch, Me, Segment, Ticket, TicketRequest, StreamKind,
} from './models';

interface EnvWindow {
  __env?: { apiUrl?: string };
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

  // ── I6 — URLs navigateur du serveur média (même origine que l'API, via Caddy) ──
  whepUrl(slug: string, kind: StreamKind): string {
    return `${this.origin}/peripheriques-webrtc/${encodeURIComponent(slug)}/${kind}/whep`;
  }

  hlsUrl(slug: string, kind: StreamKind): string {
    return `${this.origin}/peripheriques-hls/${encodeURIComponent(slug)}/${kind}/index.m3u8`;
  }
}
