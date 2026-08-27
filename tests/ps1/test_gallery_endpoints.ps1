$tests = @(
    @{ method = "GET"; path = "/"; expected = 200 },
    @{ method = "GET"; path = "/wanted"; expected = 200 },
    @{ method = "GET"; path = "/library"; expected = 200 },
    @{ method = "GET"; path = "/api/movies"; expected = 200 },
    @{ method = "GET"; path = "/api/library"; expected = 200 },
    @{ method = "GET"; path = "/api/library/status"; expected = 200 },
    @{ method = "GET"; path = "/api/library/warnings"; expected = 200 },
    @{ method = "GET"; path = "/api/library/rescan-status"; expected = 200 },
    # /api/library/{carid} —— 当前测试用临时目录，索引为空，所以不在索引的车牌应返回 404
    @{ method = "GET"; path = "/api/library/SNOS-334"; expected = 404 },
    @{ method = "GET"; path = "/api/library/INVALID"; expected = 404 },
    @{ method = "GET"; path = "/api/library/!!!"; expected = 400 },
    @{ method = "GET"; path = "/api/job/nonexistent"; expected = 404 },
    @{ method = "POST"; path = "/api/scrape"; body = '{"codes":[]}'; expected = 400 },
    @{ method = "POST"; path = "/api/scrape"; body = '{"codes":["!!!"]}'; expected = 400 },
    @{ method = "POST"; path = "/api/scrape"; body = 'not json'; expected = 400 },
    @{ method = "GET"; path = "/api/cover"; expected = 404 },
    @{ method = "GET"; path = "/api/local-cover"; expected = 400 },
    @{ method = "POST"; path = "/api/library/rescan"; expected = 200 },
    @{ method = "POST"; path = "/api/library/!!!/rescan"; expected = 400 },
    @{ method = "POST"; path = "/api/library/SNOS-334/rescan"; expected = 404 }
)
$base = "http://127.0.0.1:8000"
$passed = 0
$failed = 0
foreach ($t in $tests) {
    $uri = "$base$($t.path)"
    try {
        if ($t.method -eq "GET") {
            $r = Invoke-WebRequest -Uri $uri -UseBasicParsing -TimeoutSec 5 -ErrorAction Stop
        } else {
            $r = Invoke-WebRequest -Uri $uri -Method $t.method -Body $t.body -ContentType "application/json" -UseBasicParsing -TimeoutSec 5 -ErrorAction Stop
        }
        $code = [int]$r.StatusCode
    } catch {
        $code = [int]$_.Exception.Response.StatusCode
    }
    if ($code -eq $t.expected) {
        $passed++
        Write-Host ("[OK]   {0,4} {1}" -f $code, $t.path)
    } else {
        $failed++
        Write-Host ("[FAIL] expected={0} got={1} {2}" -f $t.expected, $code, $t.path)
    }
}
Write-Host ""
Write-Host "Passed: $passed / $($tests.Count)"
Write-Host "Failed: $failed"