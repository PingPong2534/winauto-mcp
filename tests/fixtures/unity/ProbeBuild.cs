using System;
using System.IO;
using UnityEditor;
using UnityEditor.Build.Reporting;
using UnityEditor.SceneManagement;
using UnityEngine;

public static class ProbeBuild
{
    public static void Build()
    {
        Directory.CreateDirectory("Assets/Resources");
        File.WriteAllText("Assets/Resources/ProbeTheme.tss", "@import url(\"unity-theme://default\");");
        AssetDatabase.Refresh();
        var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
        EditorSceneManager.SaveScene(scene, "Assets/Probe.unity");
        PlayerSettings.companyName = "WinAutoTest";
        PlayerSettings.productName = "WinAuto UI Toolkit Probe";
        PlayerSettings.defaultScreenWidth = 900;
        PlayerSettings.defaultScreenHeight = 650;
        PlayerSettings.fullScreenMode = FullScreenMode.Windowed;
        PlayerSettings.resizableWindow = true;
        // This fixture needs UI callbacks and pixels, not a profiler/debugger
        // listener. Release players avoid the Development Player firewall prompt.
        EditorUserBuildSettings.development = false;
        EditorUserBuildSettings.connectProfiler = false;
        EditorUserBuildSettings.allowDebugging = false;
        PlayerSettings.SetUseDefaultGraphicsAPIs(BuildTarget.StandaloneWindows64, false);
        PlayerSettings.SetGraphicsAPIs(BuildTarget.StandaloneWindows64,
            new[] {UnityEngine.Rendering.GraphicsDeviceType.Direct3D12});
        var report = BuildPipeline.BuildPlayer(new BuildPlayerOptions {
            scenes = new[] {"Assets/Probe.unity"},
            locationPathName = "Build/InteropProbe.exe",
            target = BuildTarget.StandaloneWindows64,
            options = BuildOptions.None
        });
        if (report.summary.result != BuildResult.Succeeded)
            throw new Exception("Fixture player build failed: " + report.summary.result);
    }

    public static void Open()
    {
        EditorSceneManager.OpenScene("Assets/Probe.unity");
        SessionState.SetBool("WinAutoProbeActive", true);
        EditorApplication.EnterPlaymode();
    }

    [InitializeOnLoadMethod]
    static void Register()
    {
        EditorApplication.playModeStateChanged += mode => {
            if (mode == PlayModeStateChange.EnteredPlayMode) ShowGameView();
        };
        EditorApplication.delayCall += ShowGameView;
    }

    static void ShowGameView()
    {
        if (!SessionState.GetBool("WinAutoProbeActive", false) || !EditorApplication.isPlaying) return;
        SessionState.SetBool("WinAutoProbeActive", false);
        var type = typeof(EditorWindow).Assembly.GetType("UnityEditor.GameView");
        var view = EditorWindow.GetWindow(type);
        view.Show();
        view.maximized = true;
        view.Focus();
        File.WriteAllText(Path.Combine(Application.dataPath, "../editor-ready.json"), "{\"ready\":true}");
    }
}
