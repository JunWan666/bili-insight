import { http, unwrap } from './http'
import {
  normalizeAnalysis,
  normalizeAnalysisCapabilities,
  normalizeAnalysisList,
  normalizeArtifact,
  normalizeArtifactList,
  normalizeAuthStatus,
  normalizeDownloadBatchCreation,
  normalizeDownloadCreation,
  normalizeJob,
  normalizeJobList,
  normalizeParseResponse,
  normalizePreviewSession,
  normalizeStreams,
  normalizeStreamVerification,
  normalizeStorageStatus,
  normalizeVideoResponse,
} from './adapters'
import type {
  AccessMode,
  AnalysisCapability,
  AnalysisFilters,
  AnalysisListResult,
  AnalysisRecord,
  AppSettings,
  Artifact,
  ArtifactDeleteResult,
  ArtifactFilters,
  AuthStatus,
  CreateAnalysisRequest,
  CreateDownloadBatchRequest,
  CreateDownloadRequest,
  CreatePreviewRequest,
  Diagnostics,
  EditTranscriptRequest,
  DownloadCreationResult,
  DownloadBatchCreationResult,
  Job,
  JobFilters,
  PageResult,
  ParsedVideoResult,
  PreviewSession,
  RecentVideo,
  StreamCollection,
  StreamVerificationResult,
  StorageStatus,
  VideoDetail,
} from '@/types/api'

export const videoApi = {
  async parse(url: string, accessMode: AccessMode, forceRefresh = false): Promise<ParsedVideoResult> {
    const { data } = await http.post<unknown>('/videos/parse', { url, accessMode, forceRefresh })
    return normalizeParseResponse(unwrap(data))
  },
  async get(videoId: string): Promise<VideoDetail> {
    const { data } = await http.get<unknown>(`/videos/${encodeURIComponent(videoId)}`)
    return normalizeVideoResponse(unwrap(data))
  },
  async getStreams(videoId: string, partId: string, accessMode?: Exclude<AccessMode, 'auto'>): Promise<StreamCollection> {
    const { data } = await http.get<unknown>(
      `/videos/${encodeURIComponent(videoId)}/parts/${encodeURIComponent(partId)}/streams`,
      { params: accessMode ? { accessMode } : undefined },
    )
    return normalizeStreams(unwrap(data), accessMode ? {
      requestedMode: accessMode,
      actualMode: accessMode,
      hasCredentials: accessMode === 'authenticated',
      usedAuthentication: accessMode === 'authenticated',
    } : undefined)
  },
  async verifyStream(
    streamId: string,
    accessMode: Exclude<AccessMode, 'auto'>,
  ): Promise<StreamVerificationResult> {
    const { data } = await http.post<unknown>(
      `/videos/streams/${encodeURIComponent(streamId)}/verify`,
      { accessMode },
    )
    return normalizeStreamVerification(unwrap(data))
  },
  async refresh(videoId: string, accessMode: Exclude<AccessMode, 'auto'>): Promise<VideoDetail> {
    const { data } = await http.post<unknown>(`/videos/${encodeURIComponent(videoId)}/refresh`, { accessMode })
    return normalizeVideoResponse(unwrap(data))
  },
  async recent(limit = 8): Promise<RecentVideo[]> {
    const { data } = await http.get<RecentVideo[] | { items: RecentVideo[] }>('/videos', { params: { limit } })
    return Array.isArray(data) ? data : data.items
  },
}

export const previewApi = {
  async create(request: CreatePreviewRequest): Promise<PreviewSession> {
    const { data } = await http.post<unknown>('/previews', request)
    return normalizePreviewSession(unwrap(data))
  },
  async remove(sessionId: string): Promise<void> {
    await http.delete(`/previews/${encodeURIComponent(sessionId)}`)
  },
}

export const authApi = {
  async status(): Promise<AuthStatus> {
    const { data } = await http.get<unknown>('/auth/status')
    return normalizeAuthStatus(unwrap(data))
  },
  async upload(file: File, remember: boolean): Promise<AuthStatus> {
    const form = new FormData()
    form.append('file', file, file.name)
    form.append('persistence', remember ? 'local' : 'session')
    const { data } = await http.post<unknown>('/auth/cookies', form, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 60_000,
    })
    return normalizeAuthStatus(unwrap(data))
  },
  async validate(): Promise<AuthStatus> {
    const { data } = await http.post<unknown>('/auth/validate')
    return normalizeAuthStatus(unwrap(data))
  },
  async clear(): Promise<void> {
    await http.delete('/auth/cookies')
  },
}

export const jobApi = {
  async createDownload(request: CreateDownloadRequest): Promise<DownloadCreationResult> {
    const { data } = await http.post<unknown>('/downloads', request)
    return normalizeDownloadCreation(unwrap(data))
  },
  async createDownloadBatch(request: CreateDownloadBatchRequest): Promise<DownloadBatchCreationResult> {
    const { data } = await http.post<unknown>('/downloads/batch', request)
    return normalizeDownloadBatchCreation(unwrap(data))
  },
  async createAnalysis(request: CreateAnalysisRequest): Promise<Job> {
    const { data } = await http.post<unknown>('/analyses', request)
    return normalizeJob(unwrap(data))
  },
  async list(filters: JobFilters = {}): Promise<PageResult<Job>> {
    const page = Math.max(1, filters.page ?? 1)
    const pageSize = Math.max(1, Math.min(200, filters.pageSize ?? 50))
    const { data } = await http.get<unknown>('/jobs', {
      params: {
        status: filters.status,
        type: filters.type,
        activeOnly: filters.activeOnly || undefined,
        limit: pageSize,
        offset: (page - 1) * pageSize,
      },
    })
    return normalizeJobList(unwrap(data), page, pageSize)
  },
  async get(jobId: string): Promise<Job> {
    const { data } = await http.get<unknown>(`/jobs/${encodeURIComponent(jobId)}`)
    return normalizeJob(unwrap(data))
  },
  async cancel(jobId: string): Promise<Job> {
    const { data } = await http.post<unknown>(`/jobs/${encodeURIComponent(jobId)}/cancel`)
    return normalizeJob(unwrap(data))
  },
  async retry(jobId: string): Promise<Job> {
    const { data } = await http.post<unknown>(`/jobs/${encodeURIComponent(jobId)}/retry`)
    return normalizeJob(unwrap(data))
  },
  async pause(jobId: string): Promise<Job> {
    const { data } = await http.post<unknown>(`/jobs/${encodeURIComponent(jobId)}/pause`)
    return normalizeJob(unwrap(data))
  },
  async resume(jobId: string): Promise<Job> {
    const { data } = await http.post<unknown>(`/jobs/${encodeURIComponent(jobId)}/resume`)
    return normalizeJob(unwrap(data))
  },
}

export const analysisApi = {
  async list(filters: AnalysisFilters = {}): Promise<AnalysisListResult> {
    const limit = Math.max(1, Math.min(200, filters.limit ?? 200))
    const offset = Math.max(0, filters.offset ?? 0)
    const { data } = await http.get<unknown>('/analyses', {
      params: {
        limit,
        offset,
        videoId: filters.videoId,
        partId: filters.partId,
        feature: filters.feature,
        status: filters.status,
      },
    })
    return normalizeAnalysisList(unwrap(data))
  },
  async get(analysisId: string): Promise<AnalysisRecord> {
    const { data } = await http.get<unknown>(`/analyses/${encodeURIComponent(analysisId)}`)
    return normalizeAnalysis(unwrap(data))
  },
  async editTranscript(
    analysisId: string,
    request: EditTranscriptRequest,
  ): Promise<AnalysisRecord> {
    const { data } = await http.patch<unknown>(
      `/analyses/${encodeURIComponent(analysisId)}/transcript`,
      request,
    )
    return normalizeAnalysis(unwrap(data))
  },
  async capabilities(): Promise<AnalysisCapability[]> {
    const { data } = await http.get<unknown>('/analyses/capabilities')
    return normalizeAnalysisCapabilities(unwrap(data))
  },
}

export const artifactApi = {
  async storage(): Promise<StorageStatus> {
    const { data } = await http.get<unknown>('/artifacts/storage')
    return normalizeStorageStatus(unwrap(data))
  },
  async list(filters: ArtifactFilters = {}): Promise<PageResult<Artifact>> {
    const page = Math.max(1, filters.page ?? 1)
    const pageSize = Math.max(1, Math.min(200, filters.pageSize ?? 50))
    const { data } = await http.get<unknown>('/artifacts', {
      params: {
        search: filters.query,
        type: filters.type,
        jobStatus: filters.status,
        jobId: filters.jobId,
        from: filters.from,
        to: filters.to,
        limit: pageSize,
        offset: (page - 1) * pageSize,
      },
    })
    return normalizeArtifactList(unwrap(data), page, pageSize)
  },
  async get(artifactId: string): Promise<Artifact> {
    const { data } = await http.get<unknown>(`/artifacts/${encodeURIComponent(artifactId)}`)
    return normalizeArtifact(unwrap(data))
  },
  contentUrl(artifactId: string): string {
    return `${(import.meta.env.VITE_API_BASE_URL || '/api/v1').replace(/\/$/, '')}/artifacts/${encodeURIComponent(artifactId)}/content`
  },
  async content(artifactId: string): Promise<Blob> {
    const { data } = await http.get<Blob>(`/artifacts/${encodeURIComponent(artifactId)}/content`, { responseType: 'blob' })
    return data
  },
  async remove(artifactId: string, deleteFile: boolean): Promise<ArtifactDeleteResult> {
    const { data } = await http.delete<ArtifactDeleteResult | { data: ArtifactDeleteResult }>(
      `/artifacts/${encodeURIComponent(artifactId)}`,
      { params: { deleteFile } },
    )
    return unwrap(data)
  },
}

export const settingsApi = {
  async get(): Promise<AppSettings> {
    const { data } = await http.get<AppSettings | { data: AppSettings }>('/settings')
    return unwrap(data)
  },
  async update(settings: AppSettings): Promise<AppSettings> {
    const { data } = await http.put<AppSettings | { data: AppSettings }>('/settings', settings)
    return unwrap(data)
  },
}

export const diagnosticsApi = {
  async get(): Promise<Diagnostics> {
    const { data } = await http.get<Diagnostics | { data: Diagnostics }>('/diagnostics')
    return unwrap(data)
  },
  async downloadReport(): Promise<Blob> {
    const { data } = await http.get<Blob>('/diagnostics/report', { responseType: 'blob' })
    return data
  },
}
