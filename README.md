The program is a real-time desktop math solver written in Python that captures, reads, and solves arithmetic operations and simple algebraic equations directly from any selected area of the screen.

Using a built-in crosshair selection tool, the user selects the region where math problems appear. The optical character recognition (OCR) engine continuously scans that area, extracts numbers and operators, handles negative values and unknown variables (such as ? or letters), and displays the computed result inside a lightweight, "always-on-top" floating window. Decimal values are automatically simplified into irreducible fractions.

Requirements and Setup
1. External Dependency: Tesseract OCR
Tesseract OCR (Windows Engine):

Must be installed on your system.

Important note regarding the installation path: Make sure your Tesseract installation directory matches the path defined in the script (C:\Program Files\Tesseract-OCR\tesseract.exe). If you installed Tesseract in a custom location, the code will fail to detect the engine unless you manually update the path inside calculadora.py.

Recommendation: For maximum recognition accuracy with similar numbers (such as 5 vs. 9), replace the default eng.traineddata file in your tessdata folder with the high-precision model from tessdata_best.

2. Python Environment
Python 3.8 or higher (with Tkinter included during setup).

3. Required Python Packages
Install the required dependencies via terminal.
