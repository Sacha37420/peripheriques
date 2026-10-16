import { Routes } from '@angular/router';
import { adminGuard } from './core/admin.guard';

export const routes: Routes = [
  {
    path: '',
    title: 'Direct — Périphériques',
    loadComponent: () => import('./pages/direct/direct.component').then(m => m.DirectComponent),
  },
  {
    path: 'enregistrements',
    title: 'Enregistrements — Périphériques',
    loadComponent: () => import('./pages/recordings/recordings.component').then(m => m.RecordingsComponent),
  },
  {
    path: 'droits',
    title: 'Droits — Périphériques',
    canActivate: [adminGuard],
    loadComponent: () => import('./pages/grants/grants.component').then(m => m.GrantsComponent),
  },
  {
    path: 'peripheriques',
    title: 'Périphériques — Périphériques',
    canActivate: [adminGuard],
    loadComponent: () => import('./pages/peripheriques/peripheriques.component').then(m => m.PeripheriquesComponent),
  },
  {
    path: 'materiel',
    title: 'Matériel conseillé — Périphériques',
    canActivate: [adminGuard],
    loadComponent: () => import('./pages/materiel/materiel.component').then(m => m.MaterielComponent),
  },
  { path: '**', redirectTo: '' },
];
