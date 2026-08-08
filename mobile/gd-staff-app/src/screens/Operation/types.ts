export type CalendarEvent = {
  id_evento?: number | null;
  id_lead?: number | null;
  title: string;
  start: string;
  end: string;
  location: string;
  ops: number;
  estado: string;
  marca: string;
  comuna: string;
};

export type LeadInfo = {
  id_lead: number;
  cliente: string;
  telefono: string;
  direccion: string;
  notas: string;
  fecha_evento: string;
  marca: string;
  comuna: string;
  estado: string;
  horario: string;
  duracion_horas: string;
};
