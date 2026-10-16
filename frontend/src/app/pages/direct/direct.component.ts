import { Component, OnInit, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { ApiService } from '../../core/api.service';
import { Device, StreamKind, apiErrorMessages, streamKind } from '../../core/models';
import { LivePlayerComponent } from '../../shared/live-player/live-player.component';
import { NoAccessComponent } from '../../shared/no-access/no-access.component';

interface DirectItem {
  device: Device;
  kind: StreamKind | null;
  /** Chemin à lire, pris tel quel dans l'inventaire (I1) ; null s'il n'existe pas. */
  path: string | null;
}

const KIND_LABEL: Record<StreamKind, string> = {
  av: 'Vidéo + micro',
  video: 'Vidéo seule',
  audio: 'Micro seul',
};

@Component({
  selector: 'app-direct',
  standalone: true,
  imports: [LivePlayerComponent, NoAccessComponent, RouterLink],
  templateUrl: './direct.component.html',
  styleUrl: './direct.component.scss',
})
export class DirectComponent implements OnInit {
  private api = inject(ApiService);

  readonly loading = signal(true);
  readonly errors = signal<string[]>([]);
  readonly items = signal<DirectItem[]>([]);
  readonly kindLabel = KIND_LABEL;

  ngOnInit(): void {
    this.load();
  }

  load(): void {
    this.loading.set(true);
    this.errors.set([]);
    this.api.getDevices().subscribe({
      next: devices => {
        this.items.set(devices.map(device => {
          const kind = streamKind(device);
          return { device, kind, path: kind ? device.paths[kind] : null };
        }));
        this.loading.set(false);
      },
      error: err => {
        this.errors.set(apiErrorMessages(err));
        this.loading.set(false);
      },
    });
  }
}
