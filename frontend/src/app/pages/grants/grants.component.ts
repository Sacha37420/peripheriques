import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { forkJoin } from 'rxjs';
import { ApiService } from '../../core/api.service';
import { Grant, GrantDevice, GrantPatch, apiErrorMessages } from '../../core/models';
import { fmtDateTime } from '../../core/paris-time';

type Cap = 'video' | 'audio';

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

  ngOnInit(): void {
    this.load();
  }

  load(): void {
    this.loading.set(true);
    this.loadErrors.set([]);
    forkJoin({ grants: this.api.getGrants(), devices: this.api.getGrantDevices() }).subscribe({
      next: ({ grants, devices }) => {
        this.grants.set(grants);
        this.devices.set(devices);
        if (!this.device() && devices.length) this.device.set(devices[0].slug);
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
    this.api.createGrant({
      email: this.email().trim(),
      device: this.device(),
      video: this.video(),
      audio: this.audio(),
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
    const patch: GrantPatch = cap === 'video' ? { video: next } : { audio: next };
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
