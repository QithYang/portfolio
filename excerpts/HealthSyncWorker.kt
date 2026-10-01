// Excerpt from the private Pink Anchor repository.
// Trimmed for readability.
//
// Background health sync: Health Connect -> local Room DB -> server.
// Shortened: WorkManager worker, Health Connect reader, upload step.

package app.example.memory.ui.health.data

// === SyncWorker ===
@HiltWorker
class SyncWorker @AssistedInject constructor(
    @Assisted context: Context,
    @Assisted params: WorkerParameters,
    private val repository: HealthRepository,
    private val session: SessionStore
) : CoroutineWorker(context, params) {

    override suspend fun doWork(): Result {
        if (!session.isPaired) return Result.retry()
        // Clearing app storage also revokes Health Connect access. This used to
        // fail silently; now the reason is returned so the UI can show a
        // "re-grant access" prompt with a deep link to Health Connect.
        if (!repository.hasHealthConnectPermissions()) {
            return Result.failure(workDataOf(KEY_FAIL_REASON to REASON_PERMISSION))
        }
        return try {
            repository.readAndStoreFromHealthConnect()
            repository.readAndStoreScreenTime()
            when (val r = repository.syncToServer()) {
                is SyncResult.Success, is SyncResult.NothingToSync ->
                    Result.success().also { repository.cleanupOldSamples() }
                // 401 = the device pairing is no longer valid: retrying will not help.
                is SyncResult.Error ->
                    if (r.code == 401) Result.failure(workDataOf(KEY_FAIL_REASON to REASON_AUTH))
                    else Result.retry()
            }
        } catch (e: Exception) {
            Result.retry()   // transient errors -> WorkManager backoff
        }
    }

    companion object {
        const val KEY_FAIL_REASON = "fail_reason"
        const val REASON_PERMISSION = "permission"
        const val REASON_AUTH = "auth"
        fun buildPeriodicRequest(): PeriodicWorkRequest =
            PeriodicWorkRequestBuilder<SyncWorker>(15, TimeUnit.MINUTES)
                .setConstraints(Constraints.Builder()
                    .setRequiredNetworkType(NetworkType.CONNECTED).build())
                .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 1, TimeUnit.MINUTES)
                .build()
    }
}

// === HealthConnectReader ===
@Singleton
class HealthConnectReader @Inject constructor(@ApplicationContext private val context: Context) {
    private val client by lazy { HealthConnectClient.getOrCreate(context) }

    // Read only from the one source app that carries the wearable's data;
    // the phone's own sensors are excluded so steps are not counted twice.
    private val sourceOrigin = setOf(DataOrigin(SOURCE_PACKAGE))

    val requiredPermissions = listOf(
        StepsRecord::class, HeartRateRecord::class, SleepSessionRecord::class,
        WeightRecord::class, DistanceRecord::class, ActiveCaloriesBurnedRecord::class,
        HydrationRecord::class,
    ).map { HealthPermission.getReadPermission(it) }.toSet()

    suspend fun hasAllPermissions(): Boolean {
        val granted = client.permissionController.getGrantedPermissions()
        return requiredPermissions.all { it in granted }
    }

    // Daily step buckets, aligned to local midnight, filtered by data origin.
    suspend fun readStepsAggregate(range: TimeRangeFilter): List<HealthRecordEntity> =
        client.aggregateGroupByDuration(
            AggregateGroupByDurationRequest(
                metrics = setOf(StepsRecord.COUNT_TOTAL),
                timeRangeFilter = range,
                timeRangeSlicer = Duration.ofDays(1),
                dataOriginFilter = sourceOrigin
            )
        ).mapNotNull { b ->
            val count = b.result[StepsRecord.COUNT_TOTAL] ?: return@mapNotNull null
            HealthRecordEntity(type = "steps", timestamp = b.startTime.toString(),
                value = count.toDouble())
        }

    companion object { private const val SOURCE_PACKAGE = "<source app package>" }
}

// === upload (HealthRepository) ===
suspend fun syncToServer(): SyncResult {
    val unsynced = dao.getUnsyncedRecords()
    if (unsynced.isEmpty()) return SyncResult.NothingToSync

    // Each batch carries a sync_id. The server stores processed ids and returns
    // the earlier result if the same request arrives twice (e.g. a network-level
    // retry), so a batch is never inserted twice.
    val syncId = UUID.randomUUID().toString()
    val logId = dao.insertSyncLog(SyncLogEntity(syncId = syncId, status = "started"))

    return try {
        val response = api.syncHealth(SyncRequest(unsynced.map { it.toSyncRecord() }, syncId))
        if (response.isSuccessful) {
            dao.markSynced(unsynced.map { it.id })          // only after server confirms
            val accepted = response.body()!!.accepted
            dao.updateSyncLog(logId, "success", accepted = accepted)
            SyncResult.Success(accepted)
        } else {
            dao.incrementRetryCount(unsynced.map { it.id })  // rows stay unsynced
            dao.updateSyncLog(logId, "failed", error = "HTTP ${response.code()}")
            SyncResult.Error(response.code(), response.message())
        }
    } catch (e: Exception) {   // network error: rows stay unsynced, worker retries
        dao.incrementRetryCount(unsynced.map { it.id })
        SyncResult.Error(-1, e.message ?: "unknown error")
    }
}
