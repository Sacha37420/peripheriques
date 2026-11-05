import { Component, HostListener, Input, OnDestroy, inject, signal } from '@angular/core';
import { ApiService } from '../../core/api.service';
import { KeycloakService } from '../../core/keycloak.service';

/**
 * Phase de la prise de parole :
 *   idle      prêt, personne ne parle par ce navigateur ;
 *   starting  micro demandé / négociation WHIP en cours ;
 *   talking   la voix part vers le haut-parleur (« Vous parlez ») ;
 *   busy      quelqu'un d'autre parle déjà (un seul publieur à la fois, refus de MediaMTX) ;
 *   cut       refusée ou interrompue par le serveur (droit retiré, coupure en cours) ;
 *   error     micro inaccessible, réseau, haut-parleur indisponible.
 */
type Phase = 'idle' | 'starting' | 'talking' | 'busy' | 'cut' | 'error';

const ICE_GATHER_TIMEOUT_MS = 2000;
const CONNECT_TIMEOUT_MS = 10000;

class Refused extends Error {
  constructor(readonly phase: Phase, msg: string) { super(msg); }
}

/**
 * Bouton « Parler » (contrat haut-parleur, I2) : publie le micro du navigateur en WHIP sur le
 * chemin `talk` d'un haut-parleur, avec le jeton Keycloak de l'utilisateur. Clic = commencer,
 * clic = arrêter. Tout est libéré à la sortie de la page (destruction du composant, `pagehide`) :
 * pistes du micro arrêtées, connexion fermée, session WHIP supprimée (DELETE sur `Location`).
 */
@Component({
  selector: 'app-talk-button',
  standalone: true,
  template: `
    <div class="talk" [attr.data-phase]="phase()">
      <button type="button" data-test="talk"
              [class]="active() ? 'btn btn-danger' : 'btn btn-primary'"
              [attr.aria-pressed]="active()" (click)="toggle()">
        {{ active() ? 'Arrêter de parler' : 'Parler' }}
      </button>
      @switch (phase()) {
        @case ('starting') { <p class="status" role="status">Connexion au haut-parleur…</p> }
        @case ('talking') {
          <p class="status live" role="status" data-test="talk-live">
            <span class="dot" aria-hidden="true"></span> Vous parlez : votre voix sort du haut-parleur.
          </p>
        }
        @case ('busy') { <p class="status warn" role="status" data-test="talk-busy">Quelqu’un parle déjà : réessayez dans un instant.</p> }
        @case ('cut') { <p class="error-msg" role="alert" data-test="talk-cut">{{ message() }}</p> }
        @case ('error') { <p class="error-msg" role="alert">{{ message() }}</p> }
        @default {
          @if (othersTalking) {
            <p class="status warn" data-test="talk-busy-hint">Quelqu’un parle peut-être déjà.</p>
          } @else {
            <p class="status">Rien n’est enregistré ; une seule personne parle à la fois.</p>
          }
        }
      }
    </div>
  `,
  styles: [`
    .talk { display: flex; flex-direction: column; gap: var(--space-2); align-items: flex-start; }
    .talk .btn { min-height: 44px; min-width: 44px; }
    .status { color: var(--text-mute); font-size: var(--text-sm); }
    .status.live { color: var(--text); display: flex; align-items: center; gap: var(--space-2); }
    .status.warn { color: var(--text); }
    .dot { width: 10px; height: 10px; border-radius: 50%; background: var(--accent); }
  `],
})
export class TalkButtonComponent implements OnDestroy {
  /** Chemin `talk` donné par l'inventaire (I1), jamais recomposé. */
  @Input({ required: true }) path!: string;
  /** État connu du haut-parleur (« talking » : quelqu'un parle peut-être déjà). */
  @Input() othersTalking = false;

  private api = inject(ApiService);
  private kc = inject(KeycloakService);

  readonly phase = signal<Phase>('idle');
  readonly message = signal('');

  private pc?: RTCPeerConnection;
  private stream?: MediaStream;
  private session?: string;
  private connectTimer?: ReturnType<typeof setTimeout>;
  private generation = 0;

  active(): boolean {
    return this.phase() === 'talking' || this.phase() === 'starting';
  }

  toggle(): void {
    if (this.active()) {
      this.stop();
      this.phase.set('idle');
    } else {
      void this.start();
    }
  }

  ngOnDestroy(): void {
    this.stop();
  }

  /** Fermeture de l'onglet ou navigation hors de l'app : la parole s'arrête. */
  @HostListener('window:pagehide')
  onPageHide(): void {
    this.stop();
  }

  private async start(): Promise<void> {
    const gen = ++this.generation;
    this.phase.set('starting');
    this.message.set('');
    try {
      await this.publish(gen);
    } catch (e) {
      if (gen !== this.generation) return;
      this.stop();
      if (e instanceof Refused) {
        this.message.set(e.message);
        this.phase.set(e.phase);
      } else {
        console.warn('[parler] échec :', e);
        this.message.set('Impossible de joindre le haut-parleur (réseau ou WebRTC indisponible).');
        this.phase.set('error');
      }
    }
  }

  private async publish(gen: number): Promise<void> {
    if (typeof RTCPeerConnection === 'undefined' || !navigator.mediaDevices?.getUserMedia) {
      throw new Refused('error', 'Ce navigateur ne permet pas de parler en direct (WebRTC ou micro indisponible).');
    }
    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
        video: false,
      });
    } catch {
      throw new Refused('error', 'Micro inaccessible : autorisez le micro pour ce site, puis réessayez.');
    }
    if (gen !== this.generation) {
      stream.getTracks().forEach(t => t.stop());
      return;
    }
    this.stream = stream;

    const pc = new RTCPeerConnection();
    this.pc = pc;
    for (const track of stream.getAudioTracks()) {
      pc.addTransceiver(track, { direction: 'sendonly', streams: [stream] });
    }

    const connected = new Promise<void>((resolve, reject) => {
      pc.onconnectionstatechange = () => {
        const st = pc.connectionState;
        if (st === 'connected') resolve();
        else if (st === 'failed' || st === 'disconnected' || st === 'closed') {
          if (this.phase() === 'talking' && gen === this.generation) {
            // Expulsion (droit retiré, coupure) ou perte du réseau en cours de parole.
            this.stop();
            this.message.set('Coupé : la prise de parole a été interrompue.');
            this.phase.set('cut');
          }
          reject(new Error(`WebRTC : ${st}`));
        }
      };
    });
    connected.catch(() => undefined);   // rejet traité par la course plus bas, ou sans objet

    await pc.setLocalDescription(await pc.createOffer());
    await this.iceGatheringDone(pc);

    const res = await fetch(this.api.whipUrl(this.path), {
      method: 'POST',
      headers: { 'Content-Type': 'application/sdp', Authorization: await this.bearer() },
      body: pc.localDescription!.sdp,
    });
    if (res.status === 401 || res.status === 403) {
      throw new Refused('cut', 'Coupé : la prise de parole est refusée (droit retiré ou direct coupé par un administrateur).');
    }
    if (!res.ok) {
      const text = await res.text().catch(() => '');
      if ((res.status === 400 || res.status === 409) && /already|publishing|publisher/i.test(text)) {
        throw new Refused('busy', '');
      }
      if (res.status === 404) throw new Refused('error', 'Le haut-parleur n’est pas disponible pour le moment.');
      throw new Error(`WHIP HTTP ${res.status}`);
    }
    const location = res.headers.get('Location');
    if (location) this.session = new URL(location, res.url).toString();
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
    this.phase.set('talking');
  }

  private async bearer(): Promise<string> {
    await this.kc.updateToken(30);
    return `Bearer ${this.kc.getToken() ?? ''}`;
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

  /** Libère tout, immédiatement et sans attendre : micro, connexion, session WHIP. */
  private stop(): void {
    this.generation++;
    clearTimeout(this.connectTimer);
    this.stream?.getTracks().forEach(t => t.stop());
    this.stream = undefined;
    if (this.pc) {
      this.pc.onconnectionstatechange = null;
      this.pc.close();
      this.pc = undefined;
    }
    if (this.session) {
      const url = this.session;
      this.session = undefined;
      // Fin de parole côté MediaMTX tout de suite (best-effort ; la fermeture suffit sinon).
      const token = this.kc.getToken();
      void fetch(url, {
        method: 'DELETE',
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        keepalive: true,
      }).catch(() => undefined);
    }
  }
}
