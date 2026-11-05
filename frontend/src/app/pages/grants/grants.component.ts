import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { forkJoin } from 'rxjs';
import { ApiService } from '../../core/api.service';
import { CutoffState, Grant, GrantDevice, GrantPatch, apiErrorMessages } from '../../core/models';
import { fmtDateTime } from '../../core/paris-time';

type Cap = 'video' | 'audio' | 'speaker';

@Component({
  selector: 'app-grants',
  standalone: true,
  templateUrl: './grants.component.html',
  styleUrl: './grants.component.scss',
})
export class GrantsComponent implements OnInit {
  private api = inject(ApiService);

  // ── Liste ──
  readonly loading = signal(true);
  readonly loadErrors = signal<string[]>([]);
  readonly grants = signal<Grant[]>([]);
  readonly devices = signal<GrantDevice[]>([]);
  private readonly deviceLabels = computed(
    () => new Map(this.devices().map(d => [d.slug, d.label] as const)),
  );
  private readonly deviceMics = computed(
    () => new Map(this.devices().map(d => [d.slug, d.has_audio] as const)),
  );
  private readonly deviceSpeakers = computed(
    () => new Map(this.devices().map(d => [d.slug, d.kind === 'speaker'] as const)),
  );
  /** Le périphérique choisi dans le formulaire a-t-il un micro ? (sinon : vidéo seule) */
  readonly selectedHasMic = computed(() => this.hasMic(this.device()));
  /** Le périphérique choisi est-il un haut-parleur ? (seul le droit « Parler » s'y accorde) */
  readonly selectedIsSpeaker = computed(() => this.isSpeaker(this.device()));

  // ── État par ligne ──
  readonly busy = signal<ReadonlySet<number>>(new Set());
  readonly rowErrors = signal<Record<number, string[]>>({});
  readonly confirming = signal<number | null>(null);

  // ── Formulaire ──
  readonly email = signal('');
  readonly device = signal('');
  readonly video = signal(true);
  readonly audio = signal(false);
  readonly submitting = signal(false);
  readonly formErrors = signal<string[]>([]);
  readonly formSuccess = signal('');

  // ── Coupure du direct et des enregistrements (I0) ──
  readonly cutoff = signal<CutoffState | null>(null);
  readonly cutoffErrors = signal<string[]>([]);
  readonly cutoffBusy = signal(false);
  /** Demande en attente de confirmation : true = couper, false = rétablir. */
  readonly cutoffConfirm = signal<boolean | null>(null);
  readonly stopFailures = computed(() => (this.cutoff()?.failures ?? []).filter(f => f.action === 'stop'));
  readonly restoreFailures = computed(() => (this.cutoff()?.failures ?? []).filter(f => f.action === 'restore'));
  /** Quelque chose reste à rejouer (échec d'une étape auprès du serveur média). */
  readonly cutoffIncomplete = computed(() => {
    const c = this.cutoff();
    return !!c && (c.failures.length > 0 || c.media_errors.length > 0 || (!c.active && c.paused.length > 0));
  });

  ngOnInit(): void {
    this.load();
  }

  load(): void {
    this.loadCutoff();
    this.loading.set(true);
    this.loadErrors.set([]);
    forkJoin({ grants: this.api.getGrants(), devices: this.api.getGrantDevices() }).subscribe({
      next: ({ grants, devices }) => {
        this.grants.set(grants);
        this.devices.set(devices);
        if (!devices.some(d => d.slug === this.device())) this.device.set(devices[0]?.slug ?? '');
        if (!this.selectedHasMic()) this.audio.set(false);
        this.rowErrors.set({});
        this.confirming.set(null);
        this.loading.set(false);
      },
      error: err => {
        this.loadErrors.set(apiErrorMessages(err));
        this.loading.set(false);
      },
    });
  }

  deviceLabel(slug: string): string {
    return this.deviceLabels().get(slug) ?? slug;
  }

  /** Inconnu ⇒ vrai : l'API reste juge (400 sur un droit Micro impossible). */
  hasMic(slug: string): boolean {
    return this.deviceMics().get(slug) ?? true;
  }

  /** Inconnu ⇒ faux : l'API reste juge (400 sur une capacité croisée). */
  isSpeaker(slug: string): boolean {
    return this.deviceSpeakers().get(slug) ?? false;
  }

  selectDevice(slug: string): void {
    this.device.set(slug);
    if (!this.hasMic(slug)) this.audio.set(false);
  }

  since(iso: string | null): string {
    return iso ? fmtDateTime(iso) : '—';
  }

  fmt(iso: string): string {
    return fmtDateTime(iso);
  }

  isBusy(id: number): boolean {
    return this.busy().has(id);
  }

  // ── Ajout ──
  submit(event: Event): void {
    event.preventDefault();
    if (this.submitting()) return;
    this.submitting.set(true);
    this.formErrors.set([]);
    this.formSuccess.set('');
    const speaker = this.selectedIsSpeaker();
    this.api.createGrant({
      email: this.email().trim(),
      device: this.device(),
      video: !speaker && this.video(),
      audio: !speaker && this.audio() && this.selectedHasMic(),
      speaker,
    }).subscribe({
      next: g => {
        this.upsert(g);
        this.formSuccess.set(`Droit accordé à ${g.email} sur ${this.deviceLabel(g.device)}.`);
        this.email.set('');
        this.video.set(true);
        this.audio.set(false);
        this.submitting.set(false);
      },
      error: err => {
        this.formErrors.set(apiErrorMessages(err));
        this.submitting.set(false);
      },
    });
  }

  // ── Bascule vidéo / micro ──
  toggle(g: Grant, cap: Cap, event: Event): void {
    const input = event.target as HTMLInputElement;
    const previous = g[cap];
    const next = input.checked;
    if (this.isBusy(g.id) || next === previous) {
      input.checked = previous;
      return;
    }
    const patch: GrantPatch = { [cap]: next };
    this.setBusy(g.id, true);
    this.setRowErrors(g.id, []);
    this.api.updateGrant(g.id, patch).subscribe({
      next: updated => {
        this.upsert(updated);
        this.setBusy(g.id, false);
      },
      error: err => {
        // Le signal n'a pas bougé : [checked] ne se réappliquerait pas, on remet le DOM à la main.
        input.checked = previous;
        this.setRowErrors(g.id, apiErrorMessages(err));
        this.setBusy(g.id, false);
      },
    });
  }

  // ── Révocation ──
  askRevoke(g: Grant): void {
    this.setRowErrors(g.id, []);
    this.confirming.set(g.id);
  }

  cancelRevoke(): void {
    this.confirming.set(null);
  }

  revoke(g: Grant): void {
    if (this.isBusy(g.id)) return;
    this.setBusy(g.id, true);
    this.setRowErrors(g.id, []);
    this.api.deleteGrant(g.id).subscribe({
      next: () => {
        this.grants.update(list => list.filter(x => x.id !== g.id));
        this.confirming.set(null);
        this.setBusy(g.id, false);
      },
      error: err => {
        this.setRowErrors(g.id, apiErrorMessages(err));
        this.setBusy(g.id, false);
      },
    });
  }

  // ── Coupure ──
  loadCutoff(): void {
    this.api.getCutoff().subscribe({
      next: c => { this.cutoff.set(c); this.cutoffErrors.set([]); },
      error: err => this.cutoffErrors.set(apiErrorMessages(err)),
    });
  }

  askCutoff(active: boolean): void {
    this.cutoffErrors.set([]);
    this.cutoffConfirm.set(active);
  }

  cancelCutoff(): void {
    this.cutoffConfirm.set(null);
  }

  /** Couper / rétablir, ou rejouer la même demande après un échec (aucune confirmation). */
  applyCutoff(active: boolean): void {
    if (this.cutoffBusy()) return;
    this.cutoffBusy.set(true);
    this.cutoffErrors.set([]);
    this.api.setCutoff(active).subscribe({
      next: c => {
        this.cutoff.set(c);
        this.cutoffConfirm.set(null);
        this.cutoffBusy.set(false);
      },
      error: err => {
        this.cutoffErrors.set(apiErrorMessages(err));
        this.cutoffBusy.set(false);
      },
    });
  }

  mediaErrorText(code: string): string {
    if (code === 'sessions') return 'Les lectures en cours n’ont pas toutes pu être interrompues.';
    if (code === 'inventory') return 'La liste des webcams n’a pas pu être relue : le dernier état connu a été utilisé.';
    return 'Une étape n’a pas pu joindre le serveur média.';
  }

  labels(items: { label: string }[]): string {
    return items.map(i => i.label).join(', ');
  }

  // ── Utilitaires ──
  private upsert(g: Grant): void {
    this.grants.update(list =>
      list.some(x => x.id === g.id) ? list.map(x => (x.id === g.id ? g : x)) : [...list, g],
    );
  }

  private setBusy(id: number, on: boolean): void {
    this.busy.update(s => {
      const n = new Set(s);
      if (on) n.add(id); else n.delete(id);
      return n;
    });
  }

  private setRowErrors(id: number, msgs: string[]): void {
    this.rowErrors.update(r => {
      const n = { ...r };
      if (msgs.length) n[id] = msgs; else delete n[id];
      return n;
    });
  }
}
