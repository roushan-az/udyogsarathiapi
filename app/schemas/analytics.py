# app/schemas/analytics.py
from typing import List, Dict
from pydantic import BaseModel, ConfigDict, Field

class UdyogBase(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
        populate_by_name=True,
        # Ensure React receives field names (camelCase), not aliases
        serialize_by_alias=False,
    )

class WeeklyDayRow(UdyogBase):
    day: str
    date: str
    Sales: int = 0
    Purchase: int = 0
    Inventory: int = 0
    HR: int = 0
    Finance: int = 0
    Legal: int = 0
    total: int = 0

class MonthlyStorageRow(UdyogBase):
    month: str
    year: int
    # Read 'storage_mb' from service, send 'storageMB' to React
    storageMB: float = Field(validation_alias="storage_mb")
    docCount: int = Field(validation_alias="doc_count")

class AnalyticsKPI(UdyogBase):
    totalDocuments: int
    totalStorage: int
    documentsThisWeek: int
    documentsLastWeek: int
    weekOverWeekPct: float
    avgFileSizeBytes: int
    totalPdfConversions: int
    successRate: float

class CategoryRadarRow(UdyogBase):
    subject: str
    count: int
    pct: float

class TopUploader(UdyogBase):
    name: str
    count: int
    pct: float

class AnalyticsResponse(UdyogBase):
    kpi: AnalyticsKPI
    # validation_alias matches the variable names in analytics_service.py
    weeklyTrend: List[WeeklyDayRow] = Field(validation_alias="weekly_trend")
    monthlyStorage: List[MonthlyStorageRow] = Field(validation_alias="monthly_storage")
    categoryRadar: List[CategoryRadarRow] = Field(validation_alias="category_radar")
    topUploaders: List[TopUploader] = Field(validation_alias="top_uploaders")