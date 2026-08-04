export interface ApiResponse<T> {
  data: T;
  meta?: PaginationMeta;
  error?: ApiError;
  success: boolean;
  timestamp: string;
  request_id: string;
}
export interface PaginationMeta {
  next_cursor?: string;
  has_more?: boolean;
}
export interface ApiError {
  code: string;
  message: string;
  details?: Record<string, unknown>;
}