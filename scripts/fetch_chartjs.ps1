$out = "static/js/vendor/chart.min.js"
$urls = @("https://cdn.jsdelivr.net/npm/chart.js/dist/chart.min.js","https://unpkg.com/chart.js/dist/chart.min.js")
foreach($u in $urls){
    try{
        Write-Host "Downloading $u"
        Invoke-WebRequest -Uri $u -OutFile $out -UseBasicParsing -ErrorAction Stop
        Write-Host "Saved to $out"
        exit 0
    }catch{
        Write-Host "Failed to download $u"
    }
}
Write-Host "All downloads failed. You can manually place chart.min.js into static/js/vendor/ and re-run."