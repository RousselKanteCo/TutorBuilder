from .jobs import JobUploadView, JobCheckDuplicateView, JobReuseView, JobDetailView
from .cockpit import CockpitView
from .dashboard import dashboard_view, logout_view, ProjectCreateAPIView, ProjectDeleteAPIView, JobDeleteAPIView
from .projects import ProjectListAPIView, ProjectCreateView
from .transcribe import TranscribeView
from .synthesize import SynthesizeView
from .export import ExportView, ExportStatusView
from .burn import BurnSubtitlesView
from .segments import SegmentListView, SegmentSaveView, SegmentSaveAllView, SegmentImportScriptView, SegmentAudioView