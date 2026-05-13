import client from "./client";

export interface CalendarEvent {
  id: string;
  title: string;
  start: string;
  end: string;
  source: "google" | "donna";
  calendar_id?: string;
  priority?: number;
  domain?: string;
  all_day: boolean;
}

export interface CalendarWeekResponse {
  user_id: string;
  week_start: string;
  week_end: string;
  events: CalendarEvent[];
  count: number;
  warnings?: string[];
}

export async function fetchCalendarWeek(
  date?: string,
): Promise<CalendarWeekResponse> {
  const params: Record<string, string> = {};
  if (date) params.date = date;
  const { data } = await client.get<CalendarWeekResponse>("/calendar/week", {
    params,
  });
  return data;
}
