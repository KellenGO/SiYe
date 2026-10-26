param([switch]$Probe)
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [Text.Encoding]::UTF8
[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
$ProgressPreference = 'SilentlyContinue'

function Await-Result($Operation, [Type]$ResultType) {
    $method = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
    } | Select-Object -First 1
    $task = $method.MakeGenericMethod($ResultType).Invoke($null, @($Operation))
    return $task.GetAwaiter().GetResult()
}

$stage = 'engine'
$stream = $null
$fileStream = $null
$bitmap = $null
try {
    Add-Type -AssemblyName System.Runtime.WindowsRuntime
    $null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType=WindowsRuntime]
    $null = [Windows.Media.Ocr.OcrWord, Windows.Foundation, ContentType=WindowsRuntime]
    $null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Foundation, ContentType=WindowsRuntime]
    $languages = @([Windows.Media.Ocr.OcrEngine]::AvailableRecognizerLanguages)
    $language = $languages | Where-Object { $_.LanguageTag -like 'zh-Hans*' } | Select-Object -First 1
    if (-not $language) { $language = $languages | Where-Object { $_.LanguageTag -like 'zh*' } | Select-Object -First 1 }
    if (-not $language) { $stage = 'language'; throw 'missing' }
    $engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($language)
    if (-not $engine) { throw 'unavailable' }
    if ($Probe) {
        @{ state='ok'; language=$language.LanguageTag; engine='windows-ocr' } | ConvertTo-Json -Compress
        exit 0
    }
    $inputData = [Console]::ReadLine() | ConvertFrom-Json
    $stage = 'decode'
    $fileStream = [IO.File]::OpenRead([string]$inputData.path)
    $stream = [System.IO.WindowsRuntimeStreamExtensions]::AsRandomAccessStream($fileStream)
    $decoder = Await-Result ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $width = [int]$decoder.PixelWidth
    $height = [int]$decoder.PixelHeight
    if ($width -lt 1 -or $height -lt 1 -or [long]$width * $height -gt 40000000 -or $height -gt 40000 -or $width -gt 10000) {
        $stage = 'too_large'; throw 'dimensions'
    }
    # Crop long posters instead of shrinking their small text into unreadable pixels.
    $tileHeight = [int][Math]::Min(2400, [Math]::Min([Windows.Media.Ocr.OcrEngine]::MaxImageDimension, [Math]::Floor(8000000 / $width)))
    $lines = New-Object 'System.Collections.Generic.List[string]'
    $positions = New-Object 'System.Collections.Generic.List[object]'
    $characters = 0
    $step = [Math]::Max(1, $tileHeight - 160)
    for ($top = 0; $top -lt $height; $top += $step) {
        $transform = New-Object Windows.Graphics.Imaging.BitmapTransform
        $bounds = New-Object Windows.Graphics.Imaging.BitmapBounds
        $bounds.X = 0; $bounds.Y = $top; $bounds.Width = $width; $bounds.Height = [Math]::Min($tileHeight, $height - $top)
        $transform.Bounds = $bounds
        $stage = 'decode'
        $bitmap = Await-Result ($decoder.GetSoftwareBitmapAsync(
            [Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8,
            [Windows.Graphics.Imaging.BitmapAlphaMode]::Premultiplied, $transform,
            [Windows.Graphics.Imaging.ExifOrientationMode]::IgnoreExifOrientation,
            [Windows.Graphics.Imaging.ColorManagementMode]::DoNotColorManage)) ([Windows.Graphics.Imaging.SoftwareBitmap])
        $stage = 'recognition'
        $result = Await-Result ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
        foreach ($line in $result.Lines) {
            $words = @($line.Words)
            if (-not $words.Count) { continue }
            $localY = [double]$words[0].BoundingRect.Y
            $bottom = 0.0
            for ($wordIndex = 0; $wordIndex -lt $words.Count; $wordIndex++) {
                $rect = $words[$wordIndex].BoundingRect
                $bottom = [Math]::Max($bottom, [double]$rect.Y + [double]$rect.Height)
            }
            if ($top + $bounds.Height -lt $height -and $bottom -gt $bounds.Height - 80) { continue }
            $globalY = $top + $localY
            if ($positions | Where-Object { $_.text -eq $line.Text -and [Math]::Abs($_.y - $globalY) -lt 40 }) { continue }
            if ($characters + $line.Text.Length -gt 24000) { $stage = 'text_limit'; throw 'length' }
            $lines.Add($line.Text)
            $positions.Add(@{ text=$line.Text; y=$globalY })
            $characters += $line.Text.Length
        }
        $bitmap.Dispose(); $bitmap = $null
        if ($top + $bounds.Height -ge $height) { break }
    }
    @{ state='ok'; text=($lines -join "`n"); language=$language.LanguageTag; engine='windows-ocr' } | ConvertTo-Json -Compress
} catch {
    # Never emit exception text, paths, source URLs or credentials.
    @{ state='failed'; code=$stage } | ConvertTo-Json -Compress
    exit 1
} finally {
    if ($bitmap) { $bitmap.Dispose() }
    if ($stream) { $stream.Dispose() }
    if ($fileStream) { $fileStream.Dispose() }
}
