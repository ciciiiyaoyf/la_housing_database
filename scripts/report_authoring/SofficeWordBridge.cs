using System;
using System.IO;
using System.Runtime.InteropServices;

internal static class SofficeWordBridge
{
    private static int Main(string[] args)
    {
        string inputPath = null;
        string outputDirectory = null;
        string outputFormat = null;

        for (int i = 0; i < args.Length; i++)
        {
            if (args[i] == "--outdir" && i + 1 < args.Length)
            {
                outputDirectory = args[++i];
            }
            else if (args[i] == "--convert-to" && i + 1 < args.Length)
            {
                outputFormat = args[++i].Split(':')[0].ToLowerInvariant();
            }
            else if (File.Exists(args[i]))
            {
                inputPath = Path.GetFullPath(args[i]);
            }
        }

        if (inputPath == null || outputDirectory == null || outputFormat != "pdf")
        {
            Console.Error.WriteLine("The Word rendering bridge only supports document-to-PDF conversion.");
            return 2;
        }

        Directory.CreateDirectory(outputDirectory);
        string outputPath = Path.Combine(
            Path.GetFullPath(outputDirectory),
            Path.GetFileNameWithoutExtension(inputPath) + ".pdf"
        );

        object wordObject = null;
        object documentObject = null;
        try
        {
            Type wordType = Type.GetTypeFromProgID("Word.Application");
            if (wordType == null)
            {
                Console.Error.WriteLine("Microsoft Word is not available.");
                return 3;
            }

            wordObject = Activator.CreateInstance(wordType);
            dynamic word = wordObject;
            word.Visible = false;
            word.DisplayAlerts = 0;

            documentObject = word.Documents.Open(
                inputPath,
                ConfirmConversions: false,
                ReadOnly: true,
                AddToRecentFiles: false,
                Visible: false
            );
            dynamic document = documentObject;
            document.ExportAsFixedFormat(outputPath, 17);
            document.Close(false);
            documentObject = null;
            word.Quit(false);
            wordObject = null;

            Console.WriteLine("Converted " + inputPath + " to " + outputPath);
            return 0;
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine(ex.ToString());
            return 4;
        }
        finally
        {
            if (documentObject != null)
            {
                try { ((dynamic)documentObject).Close(false); } catch { }
                try { Marshal.FinalReleaseComObject(documentObject); } catch { }
            }
            if (wordObject != null)
            {
                try { ((dynamic)wordObject).Quit(false); } catch { }
                try { Marshal.FinalReleaseComObject(wordObject); } catch { }
            }
        }
    }
}
