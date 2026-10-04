import {
  AfterViewInit, Component, ElementRef, Input, OnDestroy, ViewChild, inject, signal,
} from '@angular/core';
import type Hls from 'hls.js';
import { ApiService } from '../../core/api.service';
import { KeycloakService } from '../../core/keycloak.service';
import { StreamKind } from '../../core/models';

type Phase = 'connecting' | 'playing' | 'blocked' | 'error';
type Transport = 'webrtc' | 'hls';

/** Refus définitif du serveur média : inutile de retenter en HLS (même hook I2). */
class MediaRefused extends Error {
  constructor(readonly status: number) { super(`HTTP ${status}`); }
}

const ICE_GATHER_TIMEOUT_MS = 2000;
const CONNECT_TIMEOUT_MS = 10000;
const TOKEN_REFRESH_MS = 30000;

/**
 * Lecteur direct d'un chemin MediaMTX (I6) :
 *   1. WHEP natif (RTCPeerConnection recvonly, POST SDP offer + Bearer) ;
 *   2. repli HLS (hls.js, Bearer posé par xhrSetup) si WHEP échoue (ICE bloqué, réseau…).
 * Tout est libéré à la destruction (sortie de page) : pc.close() + DELETE de la
 * session WHEP, hls.destroy().
 */
@Component({
  selector: 'app-live-player',
  standalone: true,
  template: `
    <div class="player" [class.audio-only]="kind === 'audio'">
      @if (kind === 'audio') {
        <audio #media class="media" controls></audio>
      } @else {
        <video #media class="media" controls muted playsinline></video>
      }

      @switch (phase()) {
        @case ('connecting') {
          <p class="status" role="status">Connexion au direct…</p>
        }
        @case ('blocked') {
          <button type="button" class="btn btn-primary" (click)="resume()">
            {{ kind === 'audio' ? 'Écouter le direct' : 'Lancer la lecture' }}
          </button>
        }
        @case ('error') {
          <p class="error-msg" role="alert">{{ error() }}</p>
          <button type="button" class="btn btn-ghost" (click)="restart()">Réessayer</button>
        }
      }

      @if (phase() === 'playing' || phase() === 'blocked') {
        <p class="transport">
          {{ transport() === 'webrtc' ? 'WebRTC (temps réel)' : 'HLS (repli, quelques secondes de retard)' }}
        </p>
      }
    </div>
  `,
  styles: [`
    .player { display: flex; flex-direction: column; gap: var(--space-2); align-items: flex-start; }
    .media { width: 100%; border-radius: var(--radius-sm); background: var(--text); aspect-ratio: 16 / 9; }
    .audio-only .media { aspect-ratio: auto; background: transparent; min-height: 44px; }
    .status, .transport { color: var(--text-mute); font-size: var(--text-xs); }
  `],
})
export class LivePlayerComponent implements AfterViewInit, OnDestroy {
  @Input({ required: true }) slug!: string;
  @Input({ required: true }) kind!: StreamKind;

  @ViewChild('media') private mediaRef?: ElementRef<HTMLMediaElement>;

  private api = inject(ApiService);
  private kc = inject(KeycloakService);

  readonly phase = signal<Phase>('connecting');
  readonly transport = signal<Transport>('webrtc');
  readonly error = signal('');

  private pc?: RTCPeerConnection;
  private whepSession?: string;
  private hls?: Hls;
  private refreshTimer?: ReturnType<typeof setInterval>;
  private connectTimer?: ReturnType<typeof setTimeout>;
  private generation = 0;
  private destroyed = false;

  ngAfterViewInit(): void {
    void this.start();
  }

  ngOnDestroy(): void {
    this.destroyed = true;
    this.teardown();
  }

  restart(): void {
    this.teardown();
    void this.start();
  }

  /** Autoplay refusé par le navigateur (audio non muet) : relance sur geste utilisateur. */
  resume(): void {
    const el = this.mediaRef?.nativeElement;
    if (!el) return;
    el.play().then(() => this.phase.set('playing')).catch(() => this.phase.set('blocked'));
  }

  private async start(): Promise<void> {
    const gen = ++this.generation;
    this.phase.set('connecting');
    this.error.set('');
    // Le token Keycloak expire vite (5 min) : on le garde frais tant que le direct tourne,
    // HLS le relit à chaque requête.
    this.refreshTimer = setInterval(() => void this.kc.updateToken(60), TOKEN_REFRESH_MS);

    try {
      this.transport.set('webrtc');
      await this.startWhep(gen);
      return;
    } catch (e) {
      if (gen !== this.generation || this.destroyed) return;
      this.closeWhep();
      if (e instanceof MediaRefused) {
        this.fail(e.status === 404
          ? 'Le flux n’est pas disponible (périphérique hors ligne ?).'
          : 'Le serveur média a refusé l’accès à ce flux.');
        return;
      }
      console.warn('[direct] WHEP indisponible, repli HLS :', e);
    }

    try {
      this.transport.set('hls');
      await this.startHls(gen);
    } catch (e) {
      if (gen !== this.generation || this.destroyed) return;
      console.warn('[direct] HLS en échec :', e);
      this.fail(e instanceof MediaRefused && e.status !== 404
        ? 'Le serveur média a refusé l’accès à ce flux.'
        : 'Impossible de se connecter au direct (WebRTC puis HLS en échec).');
    }
  }

  private async bearer(): Promise<string> {
    await this.kc.updateToken(30);
    return `Bearer ${this.kc.getToken() ?? ''}`;
  }

  // ── WHEP ────────────────────────────────────────────────────────────────
  private async startWhep(gen: number): Promise<void> {
    if (typeof RTCPeerConnection === 'undefined') throw new Error('WebRTC non supporté');
    const el = this.mediaRef!.nativeElement;
    const pc = new RTCPeerConnection();
    this.pc = pc;

    if (this.kind !== 'audio') pc.addTransceiver('video', { direction: 'recvonly' });
    if (this.kind !== 'video') pc.addTransceiver('audio', { direction: 'recvonly' });

    const stream = new MediaStream();
    pc.ontrack = ev => {
      stream.addTrack(ev.track);
      el.srcObject = stream;
    };

    const connected = new Promise<void>((resolve, reject) => {
      pc.onconnectionstatechange = () => {
        if (pc.connectionState === 'connected') resolve();
        else if (pc.connectionState === 'failed') reject(new Error('ICE en échec'));
        else if (pc.connectionState === 'disconnected' && this.phase() === 'playing') {
          // Coupure en cours de lecture (révocation = kick I4, ou périphérique perdu).
          if (gen === this.generation) this.fail('Le direct a été interrompu.');
        }
      };
    });

    await pc.setLocalDescription(await pc.createOffer());
    await this.iceGatheringDone(pc);

    const res = await fetch(this.api.whepUrl(this.slug, this.kind), {
      method: 'POST',
      headers: { 'Content-Type': 'application/sdp', Authorization: await this.bearer() },
      body: pc.localDescription!.sdp,
    });
    if (res.status === 401 || res.status === 403 || res.status === 404) throw new MediaRefused(res.status);
    if (!res.ok) throw new Error(`WHEP HTTP ${res.status}`);
    const location = res.headers.get('Location');
    if (location) this.whepSession = new URL(location, res.url).toString();
    if (gen !== this.generation) throw new Error('obsolète');

    await pc.setRemoteDescription({ type: 'answer', sdp: await res.text() });

    await Promise.race([
      connected,
      new Promise<never>((_, reject) => {
        this.connectTimer = setTimeout(() => reject(new Error('ICE : délai dépassé')), CONNECT_TIMEOUT_MS);
      }),
    ]);
    clearTimeout(this.connectTimer);
    if (gen !== this.generation) throw new Error('obsolète');
    this.play(el);
  }

  private iceGatheringDone(pc: RTCPeerConnection): Promise<void> {
    if (pc.iceGatheringState === 'complete') return Promise.resolve();
    return new Promise(resolve => {
      const t = setTimeout(resolve, ICE_GATHER_TIMEOUT_MS);
      pc.addEventListener('icegatheringstatechange', () => {
        if (pc.iceGatheringState === 'complete') { clearTimeout(t); resolve(); }
      });
    });
  }

  private closeWhep(): void {
    clearTimeout(this.connectTimer);
    if (this.pc) {
      this.pc.ontrack = null;
      this.pc.onconnectionstatechange = null;
      this.pc.close();
      this.pc = undefined;
    }
    if (this.whepSession) {
      const url = this.whepSession;
      this.whepSession = undefined;
      // Libère la session côté MediaMTX tout de suite (best-effort, sinon timeout serveur).
      void this.bearer()
        .then(auth => fetch(url, { method: 'DELETE', headers: { Authorization: auth }, keepalive: true }))
        .catch(() => undefined);
    }
  }

  // ── HLS (repli) ─────────────────────────────────────────────────────────
  private async startHls(gen: number): Promise<void> {
    const { default: HlsCtor } = await import('hls.js');
    if (gen !== this.generation) return;
    if (!HlsCtor.isSupported()) throw new Error('HLS (MSE) non supporté par ce navigateur');
    const el = this.mediaRef!.nativeElement;
    el.srcObject = null;

    const hls = new HlsCtor({
      lowLatencyMode: true,
      // Le Bearer est relu à chaque requête (playlist + segments) : le hook I2 ré-autorise
      // chaque requête HLS, un token périmé couperait la lecture.
      xhrSetup: async (xhr: XMLHttpRequest, url: string) => {
        const auth = await this.bearer();
        xhr.open('GET', url, true);
        xhr.setRequestHeader('Authorization', auth);
      },
    });
    this.hls = hls;

    await new Promise<void>((resolve, reject) => {
      hls.on(HlsCtor.Events.MANIFEST_PARSED, () => resolve());
      hls.on(HlsCtor.Events.ERROR, (_ev, data) => {
        const status = data.response?.code;
        // CONTRAT v2 (I6) : en cours de lecture, 401/404 = session coupée par kick (révocation)
        // → fin de lecture, AUCUNE reconnexion. Traité même si hls.js juge l'erreur non fatale
        // (un fragment 4xx peut sinon déclencher un changement de niveau / un nouvel essai).
        const refused = status === 401 || status === 403 || status === 404;
        if (!data.fatal && !refused) return;
        hls.stopLoad();
        const err = refused ? new MediaRefused(status) : new Error(`HLS ${data.type}/${data.details}`);
        if (this.phase() === 'playing' || this.phase() === 'blocked') {
          if (gen === this.generation) this.fail('Le direct a été interrompu.');
        }
        reject(err);
      });
      hls.loadSource(this.api.hlsUrl(this.slug, this.kind));
      hls.attachMedia(el);
    });
    if (gen !== this.generation) return;
    this.play(el);
  }

  // ── commun ──────────────────────────────────────────────────────────────
  private play(el: HTMLMediaElement): void {
    el.play()
      .then(() => this.phase.set('playing'))
      .catch(() => this.phase.set('blocked'));
  }

  private fail(msg: string): void {
    this.error.set(msg);
    this.phase.set('error');
    clearInterval(this.refreshTimer);
    this.teardownTransports();
  }

  private teardownTransports(): void {
    this.closeWhep();
    this.hls?.destroy();
    this.hls = undefined;
    const el = this.mediaRef?.nativeElement;
    if (el) {
      el.pause();
      el.srcObject = null;
      el.removeAttribute('src');
    }
  }

  private teardown(): void {
    this.generation++;
    clearInterval(this.refreshTimer);
    this.teardownTransports();
  }
}
