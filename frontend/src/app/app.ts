import { Component, computed, ElementRef, HostListener, inject, signal, ViewChild } from '@angular/core';
import { NgTemplateOutlet } from '@angular/common';
import { RouterOutlet, RouterLink, RouterLinkActive } from '@angular/router';
import { KeycloakService } from './core/keycloak.service';
import { ThemeService } from './core/theme.service';
import { MeService } from './core/me.service';

interface NavItem {
  label: string;
  abbr: string;
  path: string;
  exact?: boolean;
}

const MOBILE_CLOSE_ANIM_MS = 220;

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [RouterOutlet, RouterLink, RouterLinkActive, NgTemplateOutlet],
  templateUrl: './app.html',
  styleUrl: './app.scss',
})
export class AppComponent {
  protected kc = inject(KeycloakService);
  protected theme = inject(ThemeService);

  collapsed = signal(false);
  mobileOpen = signal(false);
  mobileClosing = signal(false);

  protected noop = (): void => {};
  protected closeMobileFn = (): void => this.closeMobile();

  private meService = inject(MeService);

  /** « Droits » et « Matériel conseillé » n'apparaissent que pour un admin (/api/me/ → is_admin) ; la barrière réelle est le backend. */
  readonly navItems = computed<NavItem[]>(() => [
    { path: '/',                label: 'Direct',          abbr: 'Di', exact: true },
    { path: '/enregistrements', label: 'Enregistrements', abbr: 'En' },
    ...(this.meService.me()?.is_admin ? [
      { path: '/droits',   label: 'Droits',            abbr: 'Dr' },
      { path: '/materiel', label: 'Matériel conseillé', abbr: 'Ma' },
    ] : []),
  ]);

  constructor() {
    void this.meService.load();
  }

  @ViewChild('closeBtn') private closeBtnRef?: ElementRef<HTMLButtonElement>;
  @ViewChild('burgerBtn') private burgerBtnRef?: ElementRef<HTMLButtonElement>;

  toggleCollapsed(): void {
    this.collapsed.update(v => !v);
  }

  openMobile(): void {
    this.mobileOpen.set(true);
    this.mobileClosing.set(false);
    document.body.style.overflow = 'hidden';
    setTimeout(() => this.closeBtnRef?.nativeElement.focus());
  }

  closeMobile(): void {
    if (!this.mobileOpen() || this.mobileClosing()) return;
    this.mobileClosing.set(true);
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    setTimeout(() => {
      this.mobileOpen.set(false);
      this.mobileClosing.set(false);
      document.body.style.overflow = '';
      this.burgerBtnRef?.nativeElement.focus();
    }, reduced ? 0 : MOBILE_CLOSE_ANIM_MS);
  }

  @HostListener('document:keydown.escape')
  onEscape(): void {
    if (this.mobileOpen()) this.closeMobile();
  }

  get username(): string {
    return this.kc.username || this.kc.email;
  }

  logout(): void {
    this.kc.logout();
  }
}

// Export pour Angular standalone
export { AppComponent as App };
