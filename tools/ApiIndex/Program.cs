using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;

namespace ApiIndex
{
	/// <summary>
	/// Writes a searchable index of an assembly's public surface.
	///
	/// WHY THIS EXISTS. Answering "does Main.cloudAlpha exist" and "what does
	/// QuickSpawnItem take" has been done here by GREPPING A 21MB DLL FOR
	/// SUBSTRINGS, which finds the name and tells you nothing about what owns
	/// it, whether it is a field or a method, or what it takes. A compile
	/// answers the second question exactly, but only for code you have already
	/// written - it cannot answer "what on Main relates to rain", which is the
	/// question you have BEFORE you write anything.
	///
	/// METADATA ONLY, NEVER LOADED. MetadataLoadContext reads type and member
	/// signatures without running a static constructor, resolving a graphics
	/// device or requiring the assembly's platform. tModLoader.dll is a Windows
	/// game assembly and this runs wherever the repository's tests run.
	///
	/// The output is plain text, one member per line, because the consumer is a
	/// grep. A structured format would buy nothing a line-oriented search
	/// cannot do and would put a parser between the question and the answer.
	/// </summary>
	public static class Program
	{
		public static int Main(string[] args) {
			if (args.Length != 2) {
				Console.Error.WriteLine(
					"usage: ApiIndex <assembly.dll> <output.txt>\n" +
					"Every other assembly beside it is used to resolve the types " +
					"its signatures mention.");
				return 2;
			}

			string assemblyPath = args[0];
			string outputPath = args[1];

			if (!File.Exists(assemblyPath)) {
				Console.Error.WriteLine("no assembly at " + assemblyPath);
				return 2;
			}

			// EVERY assembly under the install, not just the ones beside it.
			// tModLoader keeps FNA, ReLogic and the rest in Libraries/, and a
			// resolver that looked only at the top level left 649 of 2240 types
			// unreadable - Terraria.Main among them, which is the single type
			// this index exists to answer questions about. Measured before and
			// after rather than reasoned about.
			var folder = Path.GetDirectoryName(Path.GetFullPath(assemblyPath));
			var assemblies = Resolvable(
				Path.GetDirectoryName(typeof(object).Assembly.Location), folder);

			var resolver = new PathAssemblyResolver(assemblies);
			using var context = new MetadataLoadContext(resolver);

			Assembly assembly = context.LoadFromAssemblyPath(Path.GetFullPath(assemblyPath));
			var sb = new StringBuilder();
			int types = 0, members = 0, skipped = 0;

			foreach (Type type in SafeTypes(assembly)) {
				bool visible;
				try {
					visible = type.IsPublic || type.IsNestedPublic;
				}
				catch (Exception) {
					// Same rule as Kind: a type this folder cannot fully resolve
					// is still a name worth finding.
					visible = true;
				}

				if (!visible) {
					continue;
				}

				types++;
				sb.Append(Full(type)).Append("\ttype\t").Append(Kind(type)).Append('\n');

				try {
					const BindingFlags Surface = BindingFlags.Public | BindingFlags.Static |
						BindingFlags.Instance | BindingFlags.DeclaredOnly;

					foreach (FieldInfo field in type.GetFields(Surface)) {
						sb.Append(Full(type)).Append('.').Append(field.Name)
							.Append("\tfield\t").Append(Full(field.FieldType)).Append('\n');
						members++;
					}

					foreach (PropertyInfo property in type.GetProperties(Surface)) {
						sb.Append(Full(type)).Append('.').Append(property.Name)
							.Append("\tproperty\t").Append(Full(property.PropertyType)).Append('\n');
						members++;
					}

					foreach (MethodInfo method in type.GetMethods(Surface)) {
						sb.Append(Full(type)).Append('.').Append(method.Name)
							.Append('(').Append(Parameters(method)).Append(')')
							.Append("\tmethod\t").Append(Full(method.ReturnType)).Append('\n');
						members++;
					}
				}
				catch (Exception) {
					// A type whose members cannot all be resolved still belongs in
					// the index under its own name; dropping it entirely would
					// make the index quietly incomplete, which is worse than a
					// type with no members listed.
					skipped++;
				}
			}

			File.WriteAllText(outputPath, sb.ToString());
			Console.WriteLine(
				$"{types} types, {members} members, {skipped} types whose members " +
				$"could not be read, from {Path.GetFileName(assemblyPath)}");
			return 0;
		}

		/// <summary>
		/// Every assembly the signatures might mention, ONE PER SIMPLE NAME.
		///
		/// PathAssemblyResolver throws outright on two files claiming the same
		/// assembly name, and a game install has plenty: tModLoader ships its
		/// OWN copy of the .NET runtime under `dotnet/`, so a recursive sweep
		/// finds a second mscorlib and the whole run dies before reading a
		/// single type. Measured, not guessed - that is exactly how this failed.
		///
		/// THE RUNTIME GOES IN FIRST and wins every collision. The core library
		/// has to be the one this process is actually running on; the game's
		/// bundled copy is for the game.
		///
		/// After that, nearest the top of the install wins, which is the copy
		/// the game itself would load.
		/// </summary>
		private static List<string> Resolvable(string runtime, string install) {
			var byName = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);

			foreach (string path in Directory.GetFiles(runtime, "*.dll")) {
				byName[Path.GetFileNameWithoutExtension(path)] = path;
			}

			var depth = new Func<string, int>(p => p.Count(c => c == Path.DirectorySeparatorChar));
			foreach (string path in Directory
					.EnumerateFiles(install, "*.dll", SearchOption.AllDirectories)
					.OrderBy(depth)) {
				string name = Path.GetFileNameWithoutExtension(path);
				if (!byName.ContainsKey(name)) {
					byName[name] = path;
				}
			}

			return byName.Values.ToList();
		}

		/// <summary>
		/// The types that CAN be read. A reference this folder does not hold
		/// makes GetTypes throw after loading most of them, and the ones it did
		/// load are still worth indexing.
		/// </summary>
		private static IEnumerable<Type> SafeTypes(Assembly assembly) {
			try {
				return assembly.GetTypes();
			}
			catch (ReflectionTypeLoadException partial) {
				return partial.Types.Where(t => t != null);
			}
		}

		/// <summary>
		/// class, struct, enum or interface — or "type" when the assemblies
		/// beside this one do not stretch far enough to say.
		///
		/// `IsEnum` walks to the BASE TYPE to answer, so a type deriving from
		/// something the folder does not hold throws rather than returning
		/// false. That is not a reason to omit the type: its NAME is what is
		/// being searched for, and a missing adjective is not a missing line.
		/// Found by running this against the real tModLoader.dll, which dumped
		/// core on the first type it could not classify.
		/// </summary>
		private static string Kind(Type type) {
			try {
				if (type.IsEnum) {
					return "enum";
				}

				if (type.IsInterface) {
					return "interface";
				}

				return type.IsValueType ? "struct" : "class";
			}
			catch (Exception) {
				return "type";
			}
		}

		private static string Parameters(MethodInfo method) {
			return string.Join(", ", method.GetParameters()
				.Select(p => Full(p.ParameterType) + " " + p.Name));
		}

		/// <summary>
		/// A type's name as somebody would search for it. Namespaces are kept -
		/// `Terraria.Main` and a mod's own `Main` are different types and an
		/// index that called both "Main" would answer the wrong one.
		/// </summary>
		private static string Full(Type type) {
			if (type == null) {
				return "?";
			}

			try {
				return type.FullName ?? type.Name;
			}
			catch (Exception) {
				return type.Name;
			}
		}
	}
}
