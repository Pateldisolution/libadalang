with Libadalang.Common; use Libadalang.Common;

package body Xrefs_Wrapper is

   function Subp_Spec_Params (Spec : Subp_Spec) return Param_Spec_List is
     (Spec.F_Subp_Params.F_Params);
   function Subp_Decl_Params (Decl : Basic_Subp_Decl) return Param_Spec_List is
     (Subp_Spec_Params (Decl.P_Subp_Decl_Spec));

   function Def_Name (BD : Basic_Decl) return Defining_Name
   is (if BD /= No_Basic_Decl then BD.P_Defining_Name else No_Defining_Name);

   ----------------------
   -- Subp_Body_Formal --
   ----------------------

   function Subp_Body_Formal (DN : Defining_Name) return Defining_Name is
      Subp_Body : Ada_Node;
      Subp_Decl : Basic_Subp_Decl;
      Decl      : constant Basic_Decl := DN.P_Basic_Decl;
   begin
      if Decl.Kind /= Ada_Param_Spec then
         return No_Defining_Name;
      end if;

      Subp_Body := Decl.P_Semantic_Parent;

      --  TODO: remove the .Is_Null check. For now, P_Semantic_Parent can
      --  return a null node in the context of a generic with rebindings.
      if Subp_Body.Is_Null or else Subp_Body.Kind /= Ada_Subp_Body then
         return No_Defining_Name;
      end if;

      declare
         Decl_Part : constant Basic_Decl := Subp_Body.As_Subp_Body.P_Decl_Part;
      begin
         if Decl_Part.Is_Null then
            return No_Defining_Name;
         elsif Decl_Part.Kind = Ada_Generic_Subp_Decl then
            Subp_Decl :=
              Decl_Part.As_Generic_Subp_Decl.F_Subp_Decl.As_Basic_Subp_Decl;
         else
            Subp_Decl := Decl_Part.As_Basic_Subp_Decl;
         end if;
      end;

      declare
         Decl_Params : constant Param_Spec_List :=
           Subp_Decl_Params (Subp_Decl);
         Formal_Index : constant Positive := Decl.Child_Index + 1;
      begin
         return Def_Name (Decl_Params.Child (Formal_Index).As_Basic_Decl);
      end;
   end Subp_Body_Formal;

   ---------------
   -- Subp_Body --
   ---------------

   function Subp_Body (DN : Defining_Name) return Defining_Name is
      Decl      : constant Basic_Decl := DN.P_Basic_Decl;
   begin
      if Decl.Kind /= Ada_Subp_Body then
         return No_Defining_Name;
      end if;

      return Def_Name (Decl.As_Subp_Body.P_Decl_Part);
   end Subp_Body;

   ---------------------
   -- Generic_Package --
   ---------------------

   function Generic_Package (DN : Defining_Name) return Defining_Name is
      Decl      : constant Basic_Decl := DN.P_Basic_Decl;
   begin
      if Decl.Kind /= Ada_Generic_Package_Decl then
         return No_Defining_Name;
      end if;

      return Def_Name
        (Decl.As_Generic_Package_Decl
         .F_Package_Decl.As_Basic_Decl);
   end Generic_Package;

   ------------------
   -- Generic_Subp --
   ------------------

   function Generic_Subp (DN : Defining_Name) return Defining_Name is
      Decl      : constant Basic_Decl := DN.P_Basic_Decl;
   begin
      if Decl.Kind /= Ada_Generic_Subp_Decl then
         return No_Defining_Name;
      end if;

      return Def_Name
        (Decl.As_Generic_Subp_Decl.F_Subp_Decl.As_Basic_Decl);
   end Generic_Subp;

   ------------------
   -- Private_Type --
   ------------------

   function Private_Type (DN : Defining_Name) return Defining_Name is
      Decl      : constant Basic_Decl := DN.P_Basic_Decl;
   begin
      if Decl.Kind not in Ada_Base_Type_Decl then
         return No_Defining_Name;
      end if;

      return Def_Name
        (Decl.As_Base_Type_Decl
         .P_Previous_Part (Go_To_Incomplete => True)
         .As_Basic_Decl);
   end Private_Type;

end Xrefs_Wrapper;
