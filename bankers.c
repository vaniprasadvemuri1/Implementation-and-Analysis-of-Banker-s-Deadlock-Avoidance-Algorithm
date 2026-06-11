/* Banker's algorithm with deadlock checks, request handling, and scenario metrics. */
#include <stdio.h>
#include <stdlib.h>
#include <stdbool.h>
#include <string.h>
#include <time.h>
#include <pthread.h>

typedef struct {
    int processes, resources;
    int *available;
    int **allocation, **max, **need;
    int *safe_sequence;
    /* scenario fields */
    int id, Q;
    int *resource_total; /* instances per type = available[j] + sum_i allocation[i][j] at load */
    int *req_p;
    int *req_data;
    bool valid, safe;
    long memory_bytes;
    double safety_time_s, total_time_s, request_time_s;
} BankersSystem;

static pthread_mutex_t report_lock;

static int **mat_new(int r, int c) {
    int **m = malloc((size_t)r * sizeof *m);
    for (int i = 0; i < r; i++) m[i] = calloc((size_t)c, sizeof **m);
    return m;
}

static void mat_free(int **m, int r) {
    for (int i = 0; i < r; i++) free(m[i]);
    free(m);
}

static bool read_vec(int *v, int n) {
    for (int i = 0; i < n; i++) if (scanf("%d", &v[i]) != 1) return false;
    return true;
}

static bool read_mat(int **m, int r, int c) {
    for (int i = 0; i < r; i++) for (int j = 0; j < c; j++) if (scanf("%d", &m[i][j]) != 1) return false;
    return true;
}

/* Need = Max - Allocation */
static void calculateNeed(BankersSystem *bs) {
    for (int i = 0; i < bs->processes; i++)
        for (int j = 0; j < bs->resources; j++)
            bs->need[i][j] = bs->max[i][j] - bs->allocation[i][j];
}

static bool validate_constraints(BankersSystem *bs) {
    for (int i = 0; i < bs->processes; i++)
        for (int j = 0; j < bs->resources; j++) {
            if (bs->allocation[i][j] < 0 || bs->max[i][j] < 0 || bs->allocation[i][j] > bs->max[i][j]) return false;
        }
    for (int j = 0; j < bs->resources; j++)
        if (bs->available[j] < 0) return false;
    calculateNeed(bs);
    for (int j = 0; j < bs->resources; j++) {
        long sum = bs->available[j];
        for (int i = 0; i < bs->processes; i++) sum += bs->allocation[i][j];
        bs->resource_total[j] = (int)sum;
    }
    return true;
}

static bool canAllocateResources(const BankersSystem *bs, int pid, const int *work) {
    for (int j = 0; j < bs->resources; j++)
        if (bs->need[pid][j] > work[j]) return false;
    return true;
}

static void print_work(const char *label, const int *work, int r) {
    printf("%s[", label);
    for (int j = 0; j < r; j++) printf("%s%d", j ? "," : "", work[j]);
    printf("]\n");
}

/* Safety algorithm with optional step trace. */
static bool run_safety_algorithm(BankersSystem *bs, bool trace, double *elapsed) {
    clock_t t0 = clock();
    int *work = malloc((size_t)bs->resources * sizeof *work);
    bool *finished = calloc((size_t)bs->processes, sizeof *finished);
    for (int j = 0; j < bs->resources; j++) work[j] = bs->available[j];
    int completed = 0;
    int step = 0;

    if (trace) {
        printf("\n--- Safety check trace ---\n");
        print_work("Initial Work = Available = ", work, bs->resources);
    }

    while (completed < bs->processes) {
        int pick = -1;
        for (int i = 0; i < bs->processes; i++) {
            if (finished[i] || !canAllocateResources(bs, i, work)) continue;
            pick = i;
            break;
        }
        if (pick < 0) break;

        if (trace) {
            printf("Step %d: P%d selected (Need[P%d] <= Work), release Allocation[P%d].\n", ++step, pick, pick, pick);
        }
        for (int j = 0; j < bs->resources; j++) work[j] += bs->allocation[pick][j];
        bs->safe_sequence[completed++] = pick;
        finished[pick] = true;
        if (trace) print_work("         Work := ", work, bs->resources);
    }

    bool safe = (completed == bs->processes);
    if (trace) {
        if (safe) {
            printf("\nResult: SAFE\n");
            printf("Safe sequence: ");
            for (int i = 0; i < bs->processes; i++) printf("P%d%s", bs->safe_sequence[i], i + 1 < bs->processes ? " => " : "");
        } else {
            printf("\nResult: UNSAFE (deadlock detected by completion test)\n");
            printf("Unfinished processes: ");
            bool first = true;
            for (int i = 0; i < bs->processes; i++) {
                if (finished[i]) continue;
                printf("%sP%d", first ? "" : ", ", i);
                first = false;
            }
            printf("\n");
            print_work("Final Work = ", work, bs->resources);
        }
        printf("--- End trace ---\n\n");
    }

    free(work);
    free(finished);
    if (elapsed) *elapsed = (double)(clock() - t0) / CLOCKS_PER_SEC;
    return safe;
}

/* Request algorithm: trial allocation, safety check, rollback if unsafe. */
static int requestResources(BankersSystem *bs, int process_id, int *request, double *elapsed) {
    clock_t t0 = clock();
    for (int j = 0; j < bs->resources; j++) {
        if (request[j] > bs->need[process_id][j]) {
            if (elapsed) *elapsed += (double)(clock() - t0) / CLOCKS_PER_SEC;
            return 1;
        }
        if (request[j] > bs->available[j]) {
            if (elapsed) *elapsed += (double)(clock() - t0) / CLOCKS_PER_SEC;
            return 2;
        }
    }
    for (int j = 0; j < bs->resources; j++) {
        bs->available[j] -= request[j];
        bs->allocation[process_id][j] += request[j];
        bs->need[process_id][j] -= request[j];
    }
    double t_safe = 0;
    bool ok = run_safety_algorithm(bs, false, &t_safe);
    bs->safety_time_s += t_safe;
    if (!ok) {
        for (int j = 0; j < bs->resources; j++) {
            bs->available[j] += request[j];
            bs->allocation[process_id][j] -= request[j];
            bs->need[process_id][j] += request[j];
        }
        if (elapsed) *elapsed += (double)(clock() - t0) / CLOCKS_PER_SEC;
        return 3;
    }
    if (elapsed) *elapsed += (double)(clock() - t0) / CLOCKS_PER_SEC;
    return 0;
}

static void printSystemState(const BankersSystem *bs) {
    printf("Allocation:\n");
    for (int i = 0; i < bs->processes; i++) {
        for (int j = 0; j < bs->resources; j++) printf("%d ", bs->allocation[i][j]);
        printf("\n");
    }
    printf("Max:\n");
    for (int i = 0; i < bs->processes; i++) {
        for (int j = 0; j < bs->resources; j++) printf("%d ", bs->max[i][j]);
        printf("\n");
    }
    printf("Need:\n");
    for (int i = 0; i < bs->processes; i++) {
        for (int j = 0; j < bs->resources; j++) printf("%d ", bs->need[i][j]);
        printf("\n");
    }
    printf("Available:");
    for (int j = 0; j < bs->resources; j++) printf(" %d", bs->available[j]);
    printf("\n");
}

/* Resource Allocation Graph edges. */
static void printRAG(const BankersSystem *bs) {
    printf("Resource Allocation Graph:\n");
    for (int i = 0; i < bs->processes; i++)
        for (int j = 0; j < bs->resources; j++)
            if (bs->allocation[i][j] > 0) printf("  R%d --(%d)--> P%d  (assignment)\n", j, bs->allocation[i][j], i);
    for (int i = 0; i < bs->processes; i++)
        for (int j = 0; j < bs->resources; j++)
            if (bs->need[i][j] > 0) printf("  P%d --(%d)--> R%d  (request / need)\n", i, bs->need[i][j], j);
}

static bool wfg_dfs_cycle(int n, int **adj, int v, int *color) {
    color[v] = 1;
    for (int u = 0; u < n; u++) {
        if (!adj[v][u]) continue;
        if (color[u] == 1) return true;
        if (!color[u] && wfg_dfs_cycle(n, adj, u, color)) return true;
    }
    color[v] = 2;
    return false;
}

static void printWaitFor(const BankersSystem *bs) {
    for (int j = 0; j < bs->resources; j++)
        if (bs->resource_total[j] != 1) {
            printf("Wait-for graph: N/A (requires single-instance resource types).\n");
            return;
        }
    int **adj = mat_new(bs->processes, bs->processes);
    for (int i = 0; i < bs->processes; i++) {
        for (int j = 0; j < bs->resources; j++) {
            if (bs->need[i][j] <= 0 || bs->available[j] > 0) continue;
            int holder = -1;
            for (int k = 0; k < bs->processes; k++)
                if (bs->allocation[k][j] > 0) {
                    holder = k;
                    break;
                }
            if (holder >= 0 && holder != i) adj[i][holder] = 1;
        }
    }
    printf("Wait-for graph (Pi waits for Pj): ");
    bool any = false;
    for (int i = 0; i < bs->processes; i++)
        for (int j = 0; j < bs->processes; j++)
            if (adj[i][j]) {
                printf("%sP%d->P%d", any ? ", " : "", i, j);
                any = true;
            }
    printf("%s\n", any ? "" : "(none)");
    int *color = calloc((size_t)bs->processes, sizeof *color);
    bool cyc = false;
    for (int i = 0; i < bs->processes; i++)
        if (!color[i] && wfg_dfs_cycle(bs->processes, adj, i, color)) {
            cyc = true;
            break;
        }
    printf("Wait-for cycle: %s\n", cyc ? "YES" : "NO");
    free(color);
    mat_free(adj, bs->processes);
}

static void bank_free(BankersSystem *bs) {
    free(bs->available);
    free(bs->safe_sequence);
    free(bs->resource_total);
    free(bs->req_p);
    free(bs->req_data);
    mat_free(bs->allocation, bs->processes);
    mat_free(bs->max, bs->processes);
    mat_free(bs->need, bs->processes);
}

static void analyze(BankersSystem *bs) {
    clock_t t0 = clock();
    bs->safety_time_s = 0;
    bs->request_time_s = 0;
    bs->valid = validate_constraints(bs);
    if (bs->valid) {
        memset(bs->safe_sequence, 0, (size_t)bs->processes * sizeof *bs->safe_sequence);
        bs->safe = run_safety_algorithm(bs, true, &bs->safety_time_s);
    } else
        bs->safe = false;
    bs->memory_bytes = (long)(2 * bs->resources + bs->processes + 3 * bs->processes * bs->resources + bs->Q * (1 + bs->resources)) *
                       (long)sizeof(int);

    pthread_mutex_lock(&report_lock);
    printf("\n======== Scenario %d (processes=%d, resources=%d) ========\n", bs->id, bs->processes, bs->resources);
    if (!bs->valid)
        printf("INVALID (allocation/max/available constraints).\n");
    printSystemState(bs);
    printRAG(bs);
    printWaitFor(bs);
    printf("Memory(bytes): %ld | Safety time(s): %.6f\n", bs->memory_bytes, bs->safety_time_s);

    for (int q = 0; q < bs->Q; q++) {
        int p = bs->req_p[q];
        int *rq = bs->req_data + q * bs->resources;
        printf("requestResources P%d [", p);
        for (int j = 0; j < bs->resources; j++) printf("%d%s", rq[j], j + 1 < bs->resources ? "," : "");
        printf("] -> ");
        if (!bs->valid) {
            printf("skipped.\n");
            continue;
        }
        if (p < 0 || p >= bs->processes) {
            printf("bad pid.\n");
            continue;
        }
        double tr = 0;
        int rc = requestResources(bs, p, rq, &tr);
        bs->request_time_s += tr;
        if (rc == 0) {
            printf("GRANTED (state remains safe).\n");
            memset(bs->safe_sequence, 0, (size_t)bs->processes * sizeof *bs->safe_sequence);
            double t_trace = 0;
            bs->safe = run_safety_algorithm(bs, true, &t_trace);
            bs->safety_time_s += t_trace;
        } else if (rc == 1)
            printf("DENIED (request exceeds Need / max claim).\n");
        else if (rc == 2)
            printf("DENIED (request > Available).\n");
        else
            printf("DENIED (unsafe after trial allocation; rolled back).\n");
    }
    bs->total_time_s = (double)(clock() - t0) / CLOCKS_PER_SEC;
    printf("Wall time(s): %.6f (request phase: %.6f)\n", bs->total_time_s, bs->request_time_s);
    pthread_mutex_unlock(&report_lock);
}

int main(void) {
    pthread_mutex_init(&report_lock, NULL);
    int S;
    if (scanf("%d", &S) != 1 || S <= 0) {
        printf("Input:\n  S\n  Each scenario: processes resources\n    Available[resources]\n"
               "    Allocation[processes][resources]\n    Max[processes][resources]\n"
               "    Q\n    Q lines: pid r0 r1 ...\n");
        pthread_mutex_destroy(&report_lock);
        return 1;
    }

    BankersSystem *sc = calloc((size_t)S, sizeof *sc);
    for (int k = 0; k < S; k++) {
        BankersSystem *bs = &sc[k];
        bs->id = k + 1;
        if (scanf("%d %d", &bs->processes, &bs->resources) != 2 || bs->processes <= 0 || bs->resources <= 0) {
            fprintf(stderr, "bad header\n");
            for (int i = 0; i < k; i++) bank_free(&sc[i]);
            free(sc);
            pthread_mutex_destroy(&report_lock);
            return 1;
        }
        bs->available = calloc((size_t)bs->resources, sizeof *bs->available);
        bs->resource_total = calloc((size_t)bs->resources, sizeof *bs->resource_total);
        bs->allocation = mat_new(bs->processes, bs->resources);
        bs->max = mat_new(bs->processes, bs->resources);
        bs->need = mat_new(bs->processes, bs->resources);
        bs->safe_sequence = calloc((size_t)bs->processes, sizeof *bs->safe_sequence);
        if (!read_vec(bs->available, bs->resources) || !read_mat(bs->allocation, bs->processes, bs->resources) ||
            !read_mat(bs->max, bs->processes, bs->resources)) {
            fprintf(stderr, "bad matrices\n");
            bank_free(bs);
            for (int i = 0; i < k; i++) bank_free(&sc[i]);
            free(sc);
            pthread_mutex_destroy(&report_lock);
            return 1;
        }
        if (scanf("%d", &bs->Q) != 1 || bs->Q < 0) bs->Q = 0;
        bs->req_p = bs->Q ? malloc((size_t)bs->Q * sizeof *bs->req_p) : NULL;
        bs->req_data = bs->Q ? calloc((size_t)bs->Q * bs->resources, sizeof *bs->req_data) : NULL;
        for (int q = 0; q < bs->Q; q++) {
            if (scanf("%d", &bs->req_p[q]) != 1 || !read_vec(bs->req_data + q * bs->resources, bs->resources)) {
                fprintf(stderr, "bad request\n");
                bank_free(bs);
                for (int i = 0; i < k; i++) bank_free(&sc[i]);
                free(sc);
                pthread_mutex_destroy(&report_lock);
                return 1;
            }
        }
    }

    printf("%-10s %-10s %-10s %-16s %-14s\n", "Scenario", "P", "R", "SafetyTime(s)", "Memory(bytes)");
    for (int k = 0; k < S; k++) {
        analyze(&sc[k]);
        printf("%-10d %-10d %-10d %-16.6f %-14ld\n", sc[k].id, sc[k].processes, sc[k].resources, sc[k].safety_time_s,
               sc[k].memory_bytes);
    }
    for (int k = 0; k < S; k++) bank_free(&sc[k]);
    free(sc);
    pthread_mutex_destroy(&report_lock);
    return 0;
}
